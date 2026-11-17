"""Experimental mixed-CAN longitudinal ownership; native admission is mandatory.

A caller must finish this transaction before publishing/construing active CP.
Shared Ioniq and generic Hyundai initialization are intentionally independent.
"""
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader

from opendbc.car.hyundai.values import CAR, is_blended, HyundaiFlags, HyundaiSafetyFlags

DISABLE = b'\x28\x03\x01'  # Unsuppressed takeover requires a matching68 03 acknowledgment.
ENABLE = b'\x28\x00\x01'  # Unsuppressed restoration: source-equivalent sent is not confirmation.


class Phase(Enum):
  IDLE = 'idle'
  PENDING = 'pending'
  OWNED = 'owned'
  FAILED = 'failed'
  RESTORING = 'restoring'
  RESTORED = 'restored'


class Outcome(Enum):
  OWNED = 'owned'
  STOCK = 'stock'
  STOCK_UNTOUCHED = 'stock_untouched'
  ABORT_UNCERTAIN = 'abort_uncertain'


@dataclass(frozen=True)
class TakeoverResult:
  outcome: Outcome

  def __bool__(self):
    raise TypeError('Inspect explicit takeover outcome; uncertain ECU state is not stock admission')

  @property
  def admissible(self):
    return self.outcome in (Outcome.OWNED, Outcome.STOCK, Outcome.STOCK_UNTOUCHED)


def alpha_eligible(cp):
  # Pinned original: classic non-legacy/non-unsupported eligibility plus the
  # dedicated HDAII allowlist, whose only member is this exact platform.
  return (is_blended(cp) and
          not cp.flags & (HyundaiFlags.CANFD | HyundaiFlags.LEGACY | HyundaiFlags.UNSUPPORTED_LONGITUDINAL | HyundaiFlags.NON_SCC) and
          not (cp.flags & HyundaiFlags.USE_FCA and not cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG))


def candidate_from_stock(cp, *, alpha_requested, native_qualified=False):
  """Only a separately qualified mixed namespace may construct a long candidate."""
  if not alpha_requested or not native_qualified or not alpha_eligible(cp) or cp.passive:
    return None
  if len(cp.safetyConfigs) != 1 or cp.openpilotLongitudinalControl or not cp.pcmCruise:
    return None
  stock_word = 0x2010 if cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else 0x2000
  if cp.safetyConfigs[0].safetyParam != stock_word:
    return None
  # get_params returns a mutable builder; convert to a reader then own a copy.
  candidate = cp.as_reader().as_builder()
  candidate.alphaLongitudinalAvailable = True
  candidate.openpilotLongitudinalControl = True
  candidate.pcmCruise = False
  from opendbc.car.hyundai.radar_interface import blended_track_radar
  candidate.radarUnavailable = cp.radarUnavailable if blended_track_radar(cp) else True
  candidate.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.LONG.value
  # Modern mixed alpha namespace2004/2014; cancel/LDA semantics are local.
  candidate.stopAccel = -.85
  return candidate


class BlendedLongitudinalOwner:
  """Explicit scoped takeover/restore; never consumes another car's saved failure."""
  def __init__(self, cp, CAN, exchange, restore_exchange=None):
    if not is_blended(cp):
      raise ValueError('Mixed-CAN owner requires the exact platform')
    self.cp = cp
    self.exchange = exchange
    if restore_exchange is None:
      from opendbc.car.hyundai.blended_disable_ecu import restore_ecu
      restore_exchange = restore_ecu
    self.restore_exchange = restore_exchange
    self.bus = CAN.ECAN
    self.address = 0x730 if cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else 0x7d0
    self.phase = Phase.IDLE
    self.cancelled = False
    self.restore_confirmed = False
    self.result = None
    self.published = False
    self.takeover_attempted = False

  def _fallback(self):
    if self.published:
      return
    self.cp.safetyConfigs[-1].safetyParam &= ~HyundaiSafetyFlags.LONG.value
    self.cp.openpilotLongitudinalControl = False
    self.cp.pcmCruise = True

  def begin(self, can_recv, can_send, *, unpublished, admission, pending_publication=False):
    if self.phase is not Phase.IDLE or self.result is not None:
      raise RuntimeError('Takeover is single-use')
    if unpublished is not True and not (pending_publication is True and self.published):
      raise RuntimeError('Takeover requires unpublished CP or explicitly sealed pending target')
    if admission is not True or not self.cp.openpilotLongitudinalControl:
      self._fallback()
      self.result = TakeoverResult(Outcome.STOCK_UNTOUCHED)
      return self.result
    self.phase = Phase.PENDING
    self.takeover_attempted = True
    try:
      accepted = self.exchange(can_recv, can_send, bus=self.bus, addr=self.address,
                               com_cont_req=DISABLE, reset=True)
    except BaseException:
      self._fallback()
      self.phase = Phase.FAILED
      self.restore(can_recv, can_send)
      raise
    if accepted is True and not self.cancelled:
      self.phase = Phase.OWNED
      self.result = TakeoverResult(Outcome.OWNED)
      return self.result
    self._fallback()
    self.phase = Phase.FAILED
    # A missing/rejected response can still follow a partially completed disable.
    restored = self.restore(can_recv, can_send)
    self.result = TakeoverResult(Outcome.STOCK if restored else Outcome.ABORT_UNCERTAIN)
    return self.result

  def cancel(self):
    self.cancelled = True

  def restore(self, can_recv, can_send):
    if self.phase is Phase.RESTORED:
      return self.restore_confirmed
    self.phase = Phase.RESTORING
    self.result = TakeoverResult(Outcome.ABORT_UNCERTAIN)
    self._fallback()
    try:
      accepted = self.restore_exchange(can_recv, can_send, bus=self.bus, addr=self.address)
    except BaseException:
      self.phase = Phase.FAILED
      raise
    self.restore_confirmed = accepted is True
    self.result = TakeoverResult(Outcome.STOCK if self.restore_confirmed else Outcome.ABORT_UNCERTAIN)
    self.phase = Phase.RESTORED if self.restore_confirmed else Phase.FAILED
    return self.restore_confirmed

  def publication_cp(self):
    if self.result is None or not self.result.admissible or (self.cancelled and self.phase is not Phase.RESTORED):
      raise RuntimeError("Uncertain ECU ownership forbids constructor/publication continuation")
    return self.cp

  def active(self):
    return self.phase is Phase.OWNED and not self.cancelled and self.cp.openpilotLongitudinalControl


class BlendedLongitudinalController:
  """Platform extension for owned diagnostic keepalive and original ACC cadence."""
  def __init__(self, owner, packer, CAN):
    self.owner = owner
    self.packer = packer
    self.CAN = CAN
    self.accel_last = 0.0
    self.normal_sent = False

  def tester_present(self, frame):
    if not self.owner.active() or self.normal_sent and frame % 100:
      return []
    from opendbc.car import make_tester_present_msg
    return [make_tester_present_msg(self.owner.address, self.owner.bus, suppress_response=True)]

  def commands(self, frame, CC, CS, accel, stopping, set_speed):
    if not self.owner.active():
      return []
    from opendbc.car.hyundai import blended_longitudinal_can as can
    cp = self.owner.cp
    hda2 = bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
    messages = can.create_blended_adrv_messages(self.packer, self.CAN, frame) if hda2 else []
    messages.extend(can.create_radar_aux_messages(self.packer, self.CAN, frame, hda2=hda2))
    if frame % 2 == 0:
      from opendbc.car import structs
      jerk = 3.0 if CC.actuators.longControlState == structs.CarControl.Actuators.LongControlState.pid else 1.0
      use_fca = cp.flags & HyundaiFlags.USE_FCA.value
      if hda2:
        messages.extend(can.create_acc_commands_can_canfd_blended_hda2(
          self.packer, CC.enabled, accel, self.accel_last, jerk, frame // 2, CC.hudControl,
          set_speed, stopping and CS.out.vEgoRaw < .1, CC.cruiseControl.override, use_fca, cp))
        self.accel_last = accel
      else:
        messages.extend(can.create_acc_commands_can_canfd_blended(
          self.packer, CC.enabled, accel, jerk, frame // 2, CC.hudControl, set_speed,
          stopping, CC.cruiseControl.override, use_fca, cp))
    return messages


def _mixed_startup_qualified_uncached(cp, *, is_release=False, allow_marked=False):
  """Exact prepared mixed owner; stock topology remains immutable."""
  from opendbc.car import structs
  from opendbc.car.hyundai.hyundaicanfd import CanBus
  declared = int(CAR.HYUNDAI_PALISADE_2023.config.flags)
  hda2 = bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
  dynamic = int(HyundaiFlags.HAS_LDA_BUTTON | HyundaiFlags.SEND_LFA)
  if hda2:
    dynamic |= int(HyundaiFlags.CANFD_LKA_STEER_MSG | HyundaiFlags.USE_FCA)
  if (is_release or int(cp.flags) & ~dynamic != declared or not alpha_eligible(cp) or cp.passive or cp.dashcamOnly or cp.notCar or
      cp.brand != 'hyundai' or cp.steerControlType != structs.CarParams.SteerControlType.torque or
      cp.alternativeExperience not in ((0, 32) if allow_marked else (0,)) or
      len(cp.safetyConfigs) != 1):
    return False
  safety = cp.safetyConfigs[0]
  word = (0x2014 if hda2 else 0x2004) if cp.openpilotLongitudinalControl and not cp.pcmCruise else (0x2010 if hda2 else 0x2000)
  buses = CanBus(cp)
  return (safety.safetyModel == structs.CarParams.SafetyModel.hyundai and safety.safetyParam == word and
          (cp.openpilotLongitudinalControl != cp.pcmCruise) and
          (buses.ECAN, buses.ACAN, buses.CAM) == ((1, 0, 2) if hda2 else (0, 1, 2)))


def hdai_startup_qualified(cp, *, is_release=False, allow_marked=False):
  return (not cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG and
          mixed_startup_qualified(cp, is_release=is_release, allow_marked=allow_marked))


def startup_required(cp):
  # Active mixed CP must never bypass its car-owned prepublication transaction.
  return is_blended(cp) and cp.openpilotLongitudinalControl


class BlendedStartup:
  def __init__(self, stock_cp, candidate, callbacks):
    import threading
    import time
    from opendbc.car.hyundai.blended_disable_ecu import confirm_disable_ecu
    from opendbc.car.hyundai.hyundaicanfd import CanBus
    self.stock_cp = stock_cp
    self.candidate_snapshot = candidate.to_dict()
    self.final_snapshot = None
    self.configured_ci = None
    self.callbacks = callbacks
    self.owner = BlendedLongitudinalOwner(candidate, CanBus(candidate), confirm_disable_ecu)
    self.stop_event = threading.Event()
    self.thread = None
    self.clock = time.monotonic
    self.started_at = None
    self.fault = None
    self.handed_off = False
    self.closed = False
    self.diagnostic_admission = None
    self.source_floor_ns = 0
    self.controller = None
    self.last_tester_at = None
    self.pending_target = False
    self.enable_gesture = None
    self.enable_credit = None
    self.enable_generation = 0
    self.enable_button = 0
    self.native_off_witness = False
    self.cruise_resume_hint = None

  def prepare(self, *, admission):
    if self.pending_target or self.owner.published or self.owner.phase is not Phase.IDLE or self.owner.result is not None:
      raise RuntimeError('Mixed startup preparation is single-use')
    self.diagnostic_admission = admission
    if admission() is not True:
      self.owner._fallback()
      self.owner.result = TakeoverResult(Outcome.STOCK_UNTOUCHED)
      return self.stock_cp
    # This is the frozen requested configuration, not ECU ownership. No
    # diagnostic suppression or command producer runs during host startup.
    self.pending_target = True
    return self.owner.cp

  def commit_pending(self, ci, *, sources_current, control_current):
    if (not self.pending_target or not self.owner.published or ci is not self.configured_ci or
        not self.prepared_for(ci.CP) or not sources_current or not control_current):
      raise RuntimeError('Mixed handoff requires the sealed pending target and current host sources')
    self.check()
    if self.diagnostic_admission is None or self.diagnostic_admission() is not True:
      raise RuntimeError('Mixed pending handoff lost diagnostic admission')
    try:
      result = self.owner.begin(*self.callbacks, unpublished=False, admission=True, pending_publication=True)
    finally:
      self.pending_target = False
    # A target CP was published. Even confirmed stock restoration cannot
    # relabel that publication as an active stock configuration: abort Card.
    if result.outcome is not Outcome.OWNED:
      raise RuntimeError('Mixed pending handoff failed; abort published target')
    import time
    self.source_floor_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    self.started_at = self.clock()
    self._tester()
    import threading

    def loop():
      while not self.stop_event.wait(.5):
        self._tester()
    self.thread = threading.Thread(target=loop, name='blended-startup-tester', daemon=True)
    self.thread.start()

  def _tester(self):
    from opendbc.car import make_tester_present_msg
    if self.started_at is None or self.fault is not None:
      return
    if not self.handed_off and self.clock() - self.started_at >= 1.:
      self.fault = 'Startup keepalive bound exceeded'
      self.owner.cancel()
      self.stop_event.set()
      return
    try:
      self.callbacks[1]([make_tester_present_msg(self.owner.address, self.owner.bus, suppress_response=True)])
      self.last_tester_at = self.clock()
    except Exception:
      self.fault = 'Startup diagnostic sender failed'
      self.owner.cancel()
      self.stop_event.set()

  def prepared_for(self, cp):
    pending = self.pending_target and self.owner.phase is Phase.IDLE and self.owner.result is None
    owned = self.owner.active() and self.owner.result is not None and self.owner.result.outcome is Outcome.OWNED and self.source_floor_ns > 0
    return (not self.closed and self.fault is None and (pending or owned) and
            self.owner.cp.to_dict() == (self.final_snapshot or self.candidate_snapshot) == cp.to_dict())

  def configure(self, ci):
    self.check()
    if self.closed or self.owner.published:
      raise RuntimeError("Mixed startup configuration is closed")
    if self.owner.active() or self.pending_target:
      self.controller = BlendedLongitudinalController(self.owner, ci.CC.packer, ci.CC.CAN)
      ci.CC.blended_longitudinal = self.controller
    self.configured_ci = ci

  def finalize_aol_configuration(self, ci):
    self.check()
    if self.closed or self.owner.published or ci is not self.configured_ci:
      raise RuntimeError('Mixed AOL finalization requires configured unpublished startup')
    if (self.owner.active() or self.pending_target) and (self.controller is None or ci.CC.blended_longitudinal is not self.controller):
      raise RuntimeError('Mixed AOL finalization requires its installed controller')
    if self.final_snapshot is not None:
      if not self.prepared_for(ci.CP):
        raise RuntimeError('Mixed AOL final configuration is frozen')
      return
    if not self.owner.active() and not self.pending_target:
      return
    expected = dict(self.candidate_snapshot)
    if ci.CP.alternativeExperience == 32:
      from opendbc.car.hyundai.blended_stock_aol import qualified_alpha
      if not qualified_alpha(ci.CP, marked_only=True):
        raise RuntimeError('Unsupported mixed alpha AOL composition')
      expected['alternativeExperience'] = 32
    if ci.CP.to_dict() != expected or self.owner.cp.to_dict() != expected:
      raise RuntimeError('Mixed alpha final CP differs from prepared transaction')
    self.final_snapshot = expected

  def seal_publication(self):
    self.check()
    if (self.owner.active() or self.pending_target) and not self.prepared_for(self.owner.cp):
      raise RuntimeError('Mixed alpha publication requires exact prepared final CP')
    self.owner.published = True

  def check(self):
    if self.fault is not None:
      raise RuntimeError(self.fault)
    if self.pending_target and self.owner.phase is Phase.IDLE and self.owner.result is None:
      return
    if self.owner.result is None or not self.owner.result.admissible:
      raise RuntimeError('Uncertain mixed-CAN ECU ownership')

  def sources_current(self, ci, now_ns, *, health_checked=False):
    from opendbc.car import Bus
    from opendbc.can.parser import MAX_BAD_COUNTER
    if not self.owner.active() and not self.pending_target:
      return True
    if self.source_floor_ns <= 0 and not self.pending_target:
      return False
    # Independent sources consumed after ECU suppression; SCC output is not a
    # takeover proof. Disabled SCC parser/source selection remains separate.
    pt_names = ('MDPS12', 'TCS11', 'TCS13', 'TCS15', 'CLU11', 'CLU15', 'ESP12',
                'CGW1', 'CGW2', 'WHL_SPD11', 'SAS11', 'EMS12', 'EMS16', 'LVR12')
    camera = 'CAM_0x2a4' if self.owner.cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else 'LKAS11'
    from opendbc.car.hyundai.hyundaicanfd import CanBus
    CAN = CanBus(self.owner.cp)
    for bus, names, expected_bus in ((Bus.pt, pt_names, CAN.ECAN), (Bus.cam, (camera,), CAN.CAM)):
      parser = ci.can_parsers[bus]
      if parser.bus != expected_bus or (not health_checked and (not parser.can_valid or parser.bus_timeout)):
        return False
      for name in names:
        address = parser.dbc.name_to_msg[name].address
        state = parser.message_states.get(address)
        if state is None or not state.timestamps or state.counter_fail >= MAX_BAD_COUNTER:
          return False
        stamp = int(state.timestamps[-1])
        if not self.source_floor_ns < stamp <= now_ns or now_ns - stamp > state.timeout_threshold:
          return False
    return True

  def after_state(self, *, ci, state, now_ns, control_enabled, control_current, pandas,
                  panda_log_ns, panda_recv_ns, panda_current):
    from opendbc.car import Bus
    from opendbc.car.hyundai.values import Buttons
    cp = self.owner.cp
    if not self.owner.active():
      self.cruise_resume_hint = None
      if self.pending_target:
        state.buttonEnable = False
        state.blockPcmEnable = True
      return
    state.buttonEnable = False
    exact = (is_blended(cp) and cp.openpilotLongitudinalControl and not cp.pcmCruise and
             len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyParam in (0x2004, 0x2014))
    matched = False
    if exact and panda_current and len(pandas) == 1:
      panda = pandas[0]
      config = cp.safetyConfigs[0]
      matched = (panda.safetyModel == config.safetyModel and panda.safetyParam == config.safetyParam and
                 panda.alternativeExperience == cp.alternativeExperience and not panda.safetyRxChecksInvalid and
                 not panda.faults and (panda.ignitionLine or panda.ignitionCan) and
                 0 < panda_log_ns <= now_ns and now_ns - panda_log_ns <= 300_000_000 and
                 0 < panda_recv_ns <= now_ns and now_ns - panda_recv_ns <= 300_000_000)
    transport_guard = (matched and control_current and state.canValid and not state.canTimeout and
                  state.cruiseState.available and not state.brakePressed and not state.gasPressed and
                  self.sources_current(ci, now_ns, health_checked=True))
    base_guard = transport_guard and not control_enabled
    parser = ci.can_parsers[Bus.pt]
    samples = list(parser.vl_all['CLU11']['CF_Clu_CruiseSwState'])
    edges = []
    for sample in samples:
      button = int(sample)
      if button != self.enable_button:
        edges.append((self.enable_button, button))
        self.enable_button = button
    if self.cruise_resume_hint is not None:
      _, hint_ns = self.cruise_resume_hint
      if (not transport_guard or edges or now_ns < hint_ns or now_ns - hint_ns > 500_000_000 or
          not ci.CS.blended_cancel_sources_current(parser, cancel=False)):
        self.cruise_resume_hint = None
    native_on = bool(matched and pandas[0].controlsAllowed)
    previous_off = self.native_off_witness
    self.native_off_witness = bool(base_guard and not native_on)
    if not base_guard:
      self.enable_gesture = None
      self.enable_credit = None
      return
    if self.enable_credit is not None:
      generation, release_ns, created_ns, button = self.enable_credit
      cancel = button == Buttons.CANCEL
      if (edges or now_ns < created_ns or now_ns - created_ns > 500_000_000 or
          not ci.CS.blended_cancel_sources_current(parser, cancel=cancel)):
        self.enable_credit = None
      elif native_on and panda_log_ns > release_ns:
        state.buttonEnable = True
        self.cruise_resume_hint = (button in (Buttons.CANCEL, Buttons.RES_ACCEL), now_ns)
        self.enable_credit = None
    for previous, button in edges:
      self.enable_credit = None
      gesture = self.enable_gesture
      self.enable_gesture = None
      if (button == Buttons.NONE and gesture is not None and previous == gesture[1] and
          ci.CS.blended_cancel_sources_current(parser, cancel=gesture[1] == Buttons.CANCEL)):
        # No immediate host event; require a subsequent fresh native ON sample.
        release_ns = parser.ts_nanos['CLU11']['CF_Clu_CruiseSwState']
        self.enable_credit = (gesture[0], release_ns, now_ns, gesture[1])
      elif (previous == Buttons.NONE and button in (Buttons.CANCEL, Buttons.RES_ACCEL, Buttons.SET_DECEL) and
            previous_off and not native_on and
            ci.CS.blended_cancel_sources_current(parser, cancel=button == Buttons.CANCEL)):
        self.enable_generation += 1
        self.enable_gesture = (self.enable_generation, button)
    if self.enable_gesture is not None:
      cancel = self.enable_gesture[1] == Buttons.CANCEL
      if not ci.CS.blended_cancel_sources_current(parser, cancel=cancel):
        self.enable_gesture = None

  def consume_cruise_resume(self):
    hint = self.cruise_resume_hint
    self.cruise_resume_hint = None
    return None if hint is None else hint[0]

  def before_control(self, *, configured, sources_current, control_current):
    self.check()
    if not self.owner.active():
      return True
    if not configured or not sources_current or not control_current:
      return False
    # Keep the worker alive until a valid controller tester actually reaches
    # PubSocket.send. Merely reaching CI.apply is not a handoff receipt.
    return True

  def maintain(self, *, configured):
    self.check()
    if not self.handed_off or not self.owner.active() or not configured:
      return
    # A missing control/input sample inhibits actuation, not ECU keepalive.
    # This runs from Card's main loop even when controls are unavailable.
    if self.last_tester_at is None or self.clock() - self.last_tester_at >= 1.:
      self._tester()

  def sent(self, frames, *, valid):
    if not valid or not self.owner.active():
      return
    from opendbc.car import make_tester_present_msg
    tester = make_tester_present_msg(self.owner.address, self.owner.bus, suppress_response=True)
    if tester not in frames:
      return
    self.last_tester_at = self.clock()
    if self.handed_off:
      return
    # Caller has released send_lock; join cannot deadlock the worker sender.
    self._stop()
    self.check()
    self.handed_off = True
    if self.controller is not None:
      self.controller.normal_sent = True

  def _stop(self):
    import threading
    self.stop_event.set()
    if self.thread is not None and self.thread is not threading.current_thread():
      self.thread.join()

  def close(self):
    if self.closed:
      if self.owner.result is not None and self.owner.result.outcome is Outcome.ABORT_UNCERTAIN:
        raise RuntimeError("Stock ECU restoration remains unverified")
      return
    self.closed = True
    self._stop()
    if not self.owner.takeover_attempted:
      return
    if self.diagnostic_admission is None or self.diagnostic_admission() is not True:
      self.owner.result = TakeoverResult(Outcome.ABORT_UNCERTAIN)
      self.owner.phase = Phase.FAILED
      raise RuntimeError("Diagnostic admission unavailable; stock ECU restoration remains unverified")
    # The output loop is stopped. Published CP is never relabeled on teardown.
    if not self.owner.restore(*self.callbacks):
      raise RuntimeError('Mixed-CAN stock ECU restoration is unverified')


def startup_owner(cp, callbacks, *, requested):
  candidate = candidate_from_stock(cp, alpha_requested=requested,
                                   native_qualified=cp.alphaLongitudinalAvailable and mixed_startup_qualified(cp))
  return None if candidate is None else BlendedStartup(cp, candidate, callbacks)


@lru_cache(maxsize=32)
def _mixed_startup_for_bytes(key, is_release, allow_marked):
  from opendbc.car import structs
  with structs.CarParams.from_bytes(key) as cp:
    return _mixed_startup_qualified_uncached(cp, is_release=is_release, allow_marked=allow_marked)


def mixed_startup_qualified(cp, *, is_release=False, allow_marked=False):
  from opendbc.car import structs
  if type(cp) in (_DynamicStructBuilder, _DynamicStructReader) and cp.schema == structs.CarParams.schema:
    try:
      snapshot = cp.copy() if type(cp) is _DynamicStructBuilder else cp.as_builder()
      key = snapshot.to_bytes()
      if len(key) <= 65536:
        return _mixed_startup_for_bytes(key, is_release, allow_marked)
    except Exception:
      pass
  return _mixed_startup_qualified_uncached(cp, is_release=is_release, allow_marked=allow_marked)
