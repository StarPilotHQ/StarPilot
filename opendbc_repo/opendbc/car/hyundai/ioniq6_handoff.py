"""Ioniq 6 HDA-II candidate and ECU-ownership transaction.

Select the exact opt-in DEBUG CarParams before publication. Verify live sources
and run the reversible diagnostic transaction when Card is ready to replace TX.
"""

import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from opendbc.car.can_definitions import CanData, CanRecvCallable, CanSendCallable
from opendbc.car import make_tester_present_msg
from opendbc.car.hyundai.hyundaicanfd import CanBus, hkg_can_fd_checksum
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


IONIQ6_ECAN_BUS = 1
IONIQ6_ADAS_ADDR = 0x730
IONIQ6_STOCK_SCC_ADDR = 0x1A0
IONIQ6_STOCK_HEARTBEAT_ADDR = 0x100
IONIQ6_STOCK_BSM = {0x1BA: 24, 0x1E5: 16}
IONIQ6_REQUIRED_RX = {0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1CF: 8}
IONIQ6_LONG_PREARM_ENABLED = True
IONIQ6_DEBUG_LONG_PARAMS = frozenset((0x8015, 0x8095, 0x8815, 0x8895))


def _ecan_bus(bus: int) -> int:
  if type(bus) is not int or bus not in (1, 5):
    raise ValueError("reviewed Ioniq 6 E-CAN bus required")
  return bus


class HandoffOutcome(Enum):
  NOT_READY = auto()
  STOCK = auto()
  CONFIRMED = auto()
  UNAVAILABLE = auto()


@dataclass(frozen=True)
class HandoffResult:
  outcome: HandoffOutcome
  reason: str
  source_period: float | None = None
  disable_acknowledged: bool = False
  restore_acknowledged: bool = False
  vehicle_ready: bool | None = None


def build_ioniq6_hda2_long_candidate(stock_cp, fingerprint):
  """Prepare a separate CP only for the two observed HDA-II EV forms."""
  from opendbc.car.hyundai.radar_interface import MRR35_RADAR_START_ADDR
  from opendbc.car.hyundai.values import CAR

  owner = config_index(stock_cp)
  if (stock_cp.carFingerprint != CAR.HYUNDAI_IONIQ_6 or stock_cp.radarUnavailable or owner is None or
      stock_cp.passive or stock_cp.dashcamOnly or stock_cp.notCar or
      stock_cp.openpilotLongitudinalControl or not stock_cp.pcmCruise):
    return None
  CAN = CanBus(stock_cp)
  if CanBus(None, fingerprint, lka_steering=True).offset != CAN.offset:
    return None
  camera = fingerprint[CAN.CAM]
  direct = camera.get(0x50) == 16 and 0x110 not in camera
  alternate = camera.get(0x110) == 32 and 0x50 not in camera
  if not (direct or alternate):
    return None
  camera_support = (0x362, 32) if alternate else (0x2A4, 24)
  if (camera.get(camera_support[0]) != camera_support[1] or
      fingerprint[CAN.ECAN].get(0x1CF) != 8 or
      fingerprint[CAN.ECAN].get(0x35) != 32 or fingerprint[CAN.ECAN].get(0x175) != 24 or
      fingerprint[CAN.ECAN].get(0xA0) != 24 or fingerprint[CAN.ECAN].get(0xEA) != 24 or
      any(fingerprint[CAN.ECAN].get(addr) != length for addr, length in {**IONIQ6_STOCK_BSM, 0x36A: 16}.items()) or
      fingerprint[CAN.ACAN].get(MRR35_RADAR_START_ADDR) != 24):
    return None
  if int(stock_cp.safetyConfigs[owner].safetyParam) not in (0x11, 0x91):
    return None

  long_cp = stock_cp.as_reader().as_builder()
  long_cp.alphaLongitudinalAvailable = True
  long_cp.openpilotLongitudinalControl = True
  long_cp.pcmCruise = False
  long_cp.safetyConfigs[owner].safetyParam = 0x8095 if alternate else 0x8015
  return long_cp


DiagnosticExchange = Callable[[bytes, bytes], bool]
Clock = Callable[[], float]


class TimestampedCanPacket(list[CanData]):
  """CAN event frames with an optional producer timestamp for cadence only."""

  def __init__(self, frames: list[CanData], log_mono_time_ns: int):
    super().__init__(frames)
    self.log_mono_time_ns = log_mono_time_ns


class ObservingCanRecv:
  """Keep SCC source evidence while ISO-TP consumes the shared CAN stream."""

  def __init__(self, can_recv: CanRecvCallable, clock: Clock = time.monotonic, *, ecan_bus: int = IONIQ6_ECAN_BUS):
    self.ecan_bus = _ecan_bus(ecan_bus)
    self.can_recv = can_recv
    self.clock = clock
    self.source_seen_count = 0
    self.latest_source_time: float | None = None
    self.heartbeat_seen_count = 0

  def __call__(self, wait_for_one: bool = False) -> list[list[CanData]]:
    packets = self.can_recv(wait_for_one=wait_for_one)
    for packet in packets:
      for msg in packet:
        if msg.src == self.ecan_bus and msg.address in (IONIQ6_STOCK_SCC_ADDR, *IONIQ6_STOCK_BSM):
          self.source_seen_count += 1
          self.latest_source_time = self.clock()
        elif msg.src == self.ecan_bus - 1 and msg.address == IONIQ6_STOCK_HEARTBEAT_ADDR:
          self.heartbeat_seen_count += 1
    return packets


def query_ioniq6_adas(can_recv: CanRecvCallable, can_send: CanSendCallable, request: bytes, response: bytes,
                      *, ecan_bus: int = IONIQ6_ECAN_BUS) -> bool:
  """Validate the service-specific positive response from 0x738 on E-CAN.

  DiagnosticSessionControl may include four P2/P2* timing bytes after 0x50 xx;
  CommunicationControl has no corresponding response payload.
  """
  try:
    query = IsoTpParallelQuery(can_send, can_recv, _ecan_bus(ecan_bus), [IONIQ6_ADAS_ADDR], [request], [response])
    payload = query.get_data(0.15, total_timeout=0.25).get((IONIQ6_ADAS_ADDR, None))
    if request in (b"\x10\x03", b"\x10\x01") and response == bytes((0x50, request[1])):
      return payload is not None and len(payload) in (0, 4)
    if request in (b"\x28\x01\x01", b"\x28\x00\x01") and response == bytes((0x68, request[1])):
      return payload == b""
    return False
  except Exception:
    return False


def _valid_canfd_crc(msg: CanData) -> bool:
  return len(msg.dat) >= 2 and int.from_bytes(msg.dat[:2], "little") == hkg_can_fd_checksum(msg.address, None, bytearray(msg.dat))


def _read_window(can_recv: CanRecvCallable, clock: Clock, duration: float, *, wait_for_one: bool = True,
                 ready_samples=None, ecan_bus: int = IONIQ6_ECAN_BUS) -> tuple[list[tuple[float, int, float]], set[int], set[int], int, bool, set[int]]:
  """Collect both stock ECU TX sources and required E-CAN input health."""
  ecan_bus = _ecan_bus(ecan_bus)
  source_samples: list[tuple[float, int, float]] = []
  healthy: set[int] = set()
  source_seen: set[int] = set()
  status_valid: set[int] = set()
  heartbeat_valid = 0
  heartbeat_seen = False
  deadline = clock() + duration
  while clock() < deadline:
    packets = can_recv(wait_for_one=wait_for_one)
    if not packets and not wait_for_one:
      # Polling preflight must also finish when the bus produces no frames.
      time.sleep(min(0.001, max(0.0, deadline - clock())))
    for packet in packets:
      received_at = clock()
      producer_ns = getattr(packet, "log_mono_time_ns", 0)
      sample_time = producer_ns * 1e-9 if producer_ns > 0 else received_at
      for msg in packet:
        if msg.src == ecan_bus - 1 and msg.address == IONIQ6_STOCK_HEARTBEAT_ADDR:
          heartbeat_seen = True
          if len(msg.dat) == 24 and _valid_canfd_crc(msg):
            heartbeat_valid += 1
          continue
        if msg.src != ecan_bus:
          continue
        if msg.address == IONIQ6_STOCK_SCC_ADDR:
          # Even a bad CRC proves the stock ECU is still transmitting.
          source_seen.add(msg.address)
          if len(msg.dat) == 32 and _valid_canfd_crc(msg):
            source_samples.append((sample_time, msg.dat[2], received_at))
        elif msg.address in IONIQ6_STOCK_BSM:
          source_seen.add(msg.address)
          if len(msg.dat) == IONIQ6_STOCK_BSM[msg.address] and _valid_canfd_crc(msg):
            status_valid.add(msg.address)
        elif msg.address == 0x35 and len(msg.dat) != 32 and ready_samples is not None:
          ready_samples.append((producer_ns, received_at, None, None, False, _ioniq6_boot_ns()))
        elif msg.address in IONIQ6_REQUIRED_RX and len(msg.dat) == IONIQ6_REQUIRED_RX[msg.address]:
          if msg.address == 0x1CF or _valid_canfd_crc(msg):
            healthy.add(msg.address)
          if msg.address == 0x35 and ready_samples is not None:
            # Preserve invalid observations: they must revoke earlier False/True.
            ready_samples.append((producer_ns, received_at, msg.dat[2], bool(msg.dat[3] & 0x40),
                                  _valid_canfd_crc(msg), _ioniq6_boot_ns()))
  return source_samples, healthy, source_seen, heartbeat_valid, heartbeat_seen, status_valid


def _measured_period(source_samples: list[tuple[float, int, float]]) -> float | None:
  if len(source_samples) < 4:
    return None
  # A queued old burst with valid producer spacing cannot establish current
  # stock source health. Fresh packets must reach this process across time.
  if source_samples[-1][2] - source_samples[0][2] < 0.08:
    return None
  if any((b[1] - a[1]) % 256 != 1 for a, b in zip(source_samples, source_samples[1:], strict=False)):
    return None
  periods = [b[0] - a[0] for a, b in zip(source_samples, source_samples[1:], strict=False)]
  period = statistics.median(periods)
  if not (0.012 <= period <= 0.030) or any(not (0.008 <= delta <= 0.050) for delta in periods):
    return None
  return period


def _ioniq6_boot_ns():
  try:
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  except (AttributeError, OSError):
    return 0


def _qualified_ioniq6_ready(samples, now):
  """Same receive window, no actuation: unknown cannot masquerade as not Ready.

  BOOTTIME bounds producer age; separate MONOTONIC receipt deltas establish
  ordered current sampling without subtracting clocks from different domains.
  """
  if len(samples) < 4 or any(not sample[4] or sample[0] <= 0 or
                             not 0 <= sample[5] - sample[0] <= 50_000_000 or sample[5] <= 0 for sample in samples):
    return None
  if samples[-1][1] - samples[0][1] < .1 or not 0 <= now - samples[-1][1] <= .05:
    return None
  values = {sample[3] for sample in samples}
  if len(values) != 1:
    return None
  for previous, current in zip(samples, samples[1:], strict=False):
    producer_delta = (current[0] - previous[0]) * 1e-9
    receipt_delta = current[1] - previous[1]
    # Producer time belongs to the CAN packet. Consecutive frames in one
    # packet share both producer and receipt time; this is not a stale burst.
    same_packet = producer_delta == 0 and receipt_delta == 0
    if (not (same_packet or 0 < producer_delta <= .05) or not 0 <= receipt_delta <= .05 or
        (current[2] - previous[2]) % 256 != 1):
      return None
  # Reject queued producer bursts even when their historical cadence is valid.
  producer_span = (samples[-1][0] - samples[0][0]) * 1e-9
  receipt_span = samples[-1][1] - samples[0][1]
  if abs(producer_span - receipt_span) > .05:
    return None
  return samples[-1][3]


def inspect_ioniq6_long_sources(can_recv: CanRecvCallable, clock: Clock = time.monotonic,
                               *, ecan_bus: int = IONIQ6_ECAN_BUS) -> HandoffResult:
  """Receive-only prerequisite check before selecting a longitudinal profile."""
  # Each attempt stands alone: never combine health or cadence across windows.
  # Drain fingerprint/retry backlog before measuring current SCC cadence.
  # Post-disable windows deliberately do not drain: any stock source matters.
  for _ in range(3):
    can_recv(wait_for_one=False)
    ready_samples = []
    pre_times, pre_health, pre_sources, pre_heartbeat_valid, _, pre_status_valid = _read_window(
      can_recv, clock, 0.25, wait_for_one=False, ready_samples=ready_samples, ecan_bus=ecan_bus)
    period = _measured_period(pre_times)
    missing = []
    if period is None:
      missing.append('scc_period')
    if pre_heartbeat_valid < 4:
      missing.append('radar_heartbeat')
    if pre_health != IONIQ6_REQUIRED_RX.keys():
      missing.append('required_rx')
    if pre_sources != {IONIQ6_STOCK_SCC_ADDR, *IONIQ6_STOCK_BSM}:
      missing.append('stock_sources')
    if pre_status_valid != IONIQ6_STOCK_BSM.keys():
      missing.append('bsm_status')
    if not missing:
      return HandoffResult(HandoffOutcome.NOT_READY, 'preflight_ready', period,
                           vehicle_ready=_qualified_ioniq6_ready(ready_samples, clock()))
  return HandoffResult(HandoffOutcome.NOT_READY, 'preflight_' + ','.join(missing))


def run_ioniq6_handoff(can_recv: CanRecvCallable, exchange: DiagnosticExchange, clock: Clock = time.monotonic,
                       *, ecan_bus: int = IONIQ6_ECAN_BUS) -> HandoffResult:
  """Resolve stock or long authority before constructing or publishing CarParams.

  The 50 Hz SCC source yields a provisional five-period silence/resumption
  window; these bounds require route calibration before feature activation.
  """
  preflight = inspect_ioniq6_long_sources(can_recv, clock, ecan_bus=ecan_bus)
  period = preflight.source_period
  if period is None:
    return preflight

  silence_window = min(max(5.0 * period, 0.10), 0.20)
  session_ack = exchange(b"\x10\x03", b"\x50\x03")
  disable_ack = session_ack and exchange(b"\x28\x01\x01", b"\x68\x01")
  if disable_ack:
    _, post_health, post_source_seen, _, post_heartbeat_seen, _ = _read_window(can_recv, clock, silence_window, ecan_bus=ecan_bus)
    if not post_source_seen and not post_heartbeat_seen and post_health == IONIQ6_REQUIRED_RX.keys():
      return HandoffResult(HandoffOutcome.CONFIRMED, "positive UDS response and observed SCC/heartbeat/BSM silence", period, True)

  # Even an unanswered 0x28 request might have taken effect. Restore both
  # normal-message TX and the normal diagnostic session before stock fallback.
  restore_tx_ack = exchange(b"\x28\x00\x01", b"\x68\x00")
  restore_session_ack = exchange(b"\x10\x01", b"\x50\x01")
  restored = restore_tx_ack and restore_session_ack
  restored_times, restored_health, restored_sources, restored_heartbeat_valid, _, restored_status_valid = _read_window(
    can_recv, clock, max(0.25, silence_window), ecan_bus=ecan_bus)
  if (restored and _measured_period(restored_times) is not None and restored_heartbeat_valid >= 4 and
      restored_health == IONIQ6_REQUIRED_RX.keys() and
      restored_sources == {IONIQ6_STOCK_SCC_ADDR, *IONIQ6_STOCK_BSM} and restored_status_valid == IONIQ6_STOCK_BSM.keys()):
    return HandoffResult(HandoffOutcome.STOCK, "restored ECU TX/session and verified stock SCC/heartbeat/BSM", period, disable_ack, True)
  return HandoffResult(HandoffOutcome.UNAVAILABLE, "ECU restore or stock SCC/heartbeat/BSM resumption unverified", period, disable_ack, restored)


def restore_ioniq6_adas(can_recv: CanRecvCallable, can_send: CanSendCallable, clock: Clock = time.monotonic,
                        *, ecan_bus: int = IONIQ6_ECAN_BUS) -> bool:
  """Abort a post-publication startup without ever claiming stock CP ownership."""
  observed_recv = ObservingCanRecv(can_recv, clock, ecan_bus=ecan_bus)
  restore_tx = query_ioniq6_adas(observed_recv, can_send, b"\x28\x00\x01", b"\x68\x00", ecan_bus=ecan_bus)
  restore_session = query_ioniq6_adas(observed_recv, can_send, b"\x10\x01", b"\x50\x01", ecan_bus=ecan_bus)
  samples, healthy, sources, heartbeat_valid, _, status_valid = _read_window(observed_recv, clock, 0.25, ecan_bus=ecan_bus)
  return (restore_tx and restore_session and _measured_period(samples) is not None and heartbeat_valid >= 4 and
          healthy == IONIQ6_REQUIRED_RX.keys() and sources == {IONIQ6_STOCK_SCC_ADDR, *IONIQ6_STOCK_BSM} and
          status_valid == IONIQ6_STOCK_BSM.keys())


class Ioniq6StartupKeepalive:
  """Maintain the acknowledged diagnostic session until Panda takes TX.

  A conservative 0.5-second diagnostic interval keeps the prepublication
  session alive before the controller's normal 1 Hz tester takes ownership.
  The 60-second startup bound is a development fail-closed limit, not a measured
  ECU session lifetime. Production enablement needs parked/device timing.
  """

  def __init__(self, can_send: CanSendCallable, restore: Callable[[], bool], on_abort: Callable[[], None],
               on_restore: Callable[[bool], None], clock: Clock = time.monotonic, max_startup_sec: float = 60.0,
               *, ecan_bus: int = IONIQ6_ECAN_BUS):
    self.ecan_bus = _ecan_bus(ecan_bus)
    self.can_send = can_send
    self.restore = restore
    self.on_abort = on_abort
    self.on_restore = on_restore
    self.clock = clock
    self.max_startup_sec = max_startup_sec
    self.started_at = clock()
    self.next_tester_at = self.started_at
    self.stop_event = threading.Event()
    self.lock = threading.Lock()
    self.thread: threading.Thread | None = None
    self.completed = False

  def tick(self):
    with self.lock:
      if self.completed or self.stop_event.is_set():
        return
      now = self.clock()
      if now - self.started_at >= self.max_startup_sec:
        self._abort_locked()
      elif now >= self.next_tester_at:
        try:
          self.can_send([make_tester_present_msg(IONIQ6_ADAS_ADDR, self.ecan_bus, suppress_response=True)])
          self.next_tester_at = now + 0.5
        except Exception:
          self._abort_locked()

  def _abort_locked(self):
    self.completed = True
    # The caller permanently suppresses controller TX before restoration.
    self.on_abort()
    try:
      restored = self.restore()
    except Exception:
      restored = False
    self.on_restore(restored)

  def start(self):
    def loop():
      while not self.stop_event.wait(0.05) and not self.completed:
        self.tick()

    self.tick()
    self.thread = threading.Thread(target=loop, name="ioniq6-prearm-tester", daemon=True)
    self.thread.start()

  def stop(self):
    # This is the TX ownership boundary: no keepalive or restore is in flight
    # when the caller is allowed to submit its first controller frame.
    self.stop_event.set()
    if self.thread is not None and self.thread is not threading.current_thread():
      self.thread.join()
    with self.lock:
      self.completed = True


def finalize_ioniq6_prepublication(stock_cp, long_cp, can_recv: CanRecvCallable, can_send: CanSendCallable,
                                  *, enabled: bool, is_release: bool, clock: Clock = time.monotonic):
  """Choose a prepared CP before CarInterface exists; never mutate it after arm.

  The DEBUG candidate must already be built and validated before any UDS request.
  """
  if not enabled or is_release or long_cp is None:
    return stock_cp, False, None
  raw = int(long_cp.safetyConfigs[-1].safetyParam)
  if config_index(long_cp) is None or not long_cp.openpilotLongitudinalControl or raw not in IONIQ6_DEBUG_LONG_PARAMS:
    raise ValueError("unreviewed Ioniq 6 longitudinal CarParams profile")

  ecan_bus = _ecan_bus(CanBus(long_cp, lka_steering=True).ECAN)
  observed_recv = ObservingCanRecv(can_recv, clock, ecan_bus=ecan_bus)
  result = run_ioniq6_handoff(
    observed_recv, lambda request, response: query_ioniq6_adas(observed_recv, can_send, request, response, ecan_bus=ecan_bus), clock, ecan_bus=ecan_bus)
  if result.outcome is HandoffOutcome.CONFIRMED:
    return long_cp, True, result
  if result.outcome is HandoffOutcome.UNAVAILABLE:
    # Card's usual passive setup will publish noOutput; do not advertise stock
    # cruise ownership when the stock SCC ECU may still be silent.
    stock_cp.dashcamOnly = True
  return stock_cp, False, result


def prepare_ioniq6_long_candidate(stock_cp, long_cp, *, enabled: bool, is_release: bool):
  """Select the immutable long CP without changing ECU communication yet."""
  if not enabled or is_release or long_cp is None:
    return stock_cp, False
  raw = int(long_cp.safetyConfigs[-1].safetyParam)
  if config_index(long_cp) is None or not long_cp.openpilotLongitudinalControl or raw not in IONIQ6_DEBUG_LONG_PARAMS:
    raise ValueError("unreviewed Ioniq 6 longitudinal CarParams profile")
  return long_cp, True


def confirm_ioniq6_prepared_takeover(can_recv: CanRecvCallable, can_send: CanSendCallable,
                                     clock: Clock = time.monotonic, *, ecan_bus: int = IONIQ6_ECAN_BUS) -> HandoffResult:
  """Run the source/UDS transaction only when replacement TX can start."""
  observed_recv = ObservingCanRecv(can_recv, clock, ecan_bus=ecan_bus)
  return run_ioniq6_handoff(
    observed_recv, lambda request, response: query_ioniq6_adas(observed_recv, can_send, request, response, ecan_bus=ecan_bus), clock, ecan_bus=ecan_bus)
