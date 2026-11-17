from contextlib import contextmanager
from copy import deepcopy
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import Bus, CanData, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ev9_longitudinal import candidate, qualified
from opendbc.car.hyundai.ev9_pending_startup import EV9PendingStartup as PEPendingStartup
from opendbc.car.hyundai.ioniq5pe_startup import Lease, Phase
from opendbc.car.hyundai.ioniq5pe_ready import PEReadySource
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params as stock_params
from opendbc.car.hyundai.tests.test_ev9_long_controller import packets
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket
from openpilot.selfdrive.car.card import Car


def params():
  return stock_params(candidate=CAR.KIA_EV9)


class ThreadFixture:
  def __init__(self, **kwargs):
    self.kwargs = kwargs
    self.started = self.joined = False

  def start(self):
    self.started = True

  def join(self):
    self.joined = True


class EV9StartupFixture(unittest.TestCase):
  def setUp(self):
    self.boot = 1_000_000_000
    self.seconds = 1.
    self.sent = []
    self.queries = []
    self.admitted = True
    self.disable_ok = True
    self.restore_ok = True
    self.tick = 0
    boot = patch('opendbc.car.hyundai.ioniq5pe_ready.clock_boot_ns', side_effect=lambda: self.boot)
    boot.start()
    self.addCleanup(boot.stop)
    self.stock = params()
    self.stock_before = self.stock.to_dict()
    self.cp = candidate(self.stock, enabled=True, is_release=False)
    self.assertTrue(qualified(self.cp))

  def exchange(self, recv, send, request, response):
    self.queries.append((request, response))
    if request == b'\x28\x01\x01':
      return self.disable_ok
    if request == b'\x28\x00\x01':
      return self.restore_ok
    return (request, response) in ((b'\x10\x03', b'\x50\x03'), (b'\x10\x01', b'\x50\x01'))

  def instance(self, ready=False, *, seal=True):
    packer = CANPacker(DBC[CAR.KIA_EV9][Bus.pt])

    def recv(**kwargs):

      self.tick += 1
      self.boot += 10_000_000
      self.seconds += .01
      return [TimestampedCanPacket(packets(packer, self.tick, advance_counter=True), self.boot)]

    owner = PEPendingStartup(self.cp, (recv, self.sent.extend), stock_cp=self.stock,
                             clock=lambda: self.seconds, boot_clock=lambda: self.boot,
                             exchange=self.exchange, thread_factory=ThreadFixture)
    with patch.object(owner, '_ready_probe', return_value=ready):
      cp = owner.prepare(admission=lambda: self.admitted)
    ci = CarInterface(cp)
    owner.configure(ci)
    owner.finalize_aol_configuration(ci)
    if seal:
      owner.seal_publication()
    return owner, ci, packer

  def warm(self, owner, ci, packer):
    for _ in range(12):
      self.tick += 1
      self.boot += 10_000_000
      ci.update([(self.boot, packets(packer, self.tick, advance_counter=True))])
    self.assertTrue(ci.CS.out.canValid)
    self.assertTrue(owner.sources_current(ci, self.boot))

  def acknowledge(self, owner, ci):
    self.admitted = False
    config = ci.CP.safetyConfigs[0]
    panda = SimpleNamespace(safetyModel=config.safetyModel, safetyParam=config.safetyParam,
                            alternativeExperience=0, safetyRxChecksInvalid=False, controlsAllowed=False,
                            faults=[], ignitionLine=True, ignitionCan=False)
    owner.after_state(ci=ci, state=ci.CS.out, now_ns=self.boot, control_enabled=False,
                      control_current=True, pandas=[panda], panda_log_ns=self.boot,
                      panda_recv_ns=self.boot, panda_current=True)

  def commit(self, owner, ci):
    owner.commit_pending(ci, sources_current=owner.sources_current(ci, self.boot), control_current=True)


@contextmanager
def pending_startup_fixture():
  fixture = EV9StartupFixture()
  fixture.setUp()
  try:
    owner, ci, packer = fixture.instance()
    assert owner.pending_target and owner.owner.published
    assert fixture.queries == [] and fixture.sent == [] and owner.thread is None
    yield fixture, owner, ci, packer
  finally:
    fixture.doCleanups()


class TestEV9PendingStartup(EV9StartupFixture, unittest.TestCase):
  def test_prepare_preserves_factory_output_and_never_sends_disable_or_keeper(self):
    owner, ci, _ = self.instance()
    self.assertTrue(owner.pending_target)
    self.assertFalse(owner.owner.active())
    self.assertEqual(self.queries, [])
    self.assertEqual(self.sent, [])
    self.assertIsNone(owner.thread)
    self.assertEqual(self.stock.to_dict(), self.stock_before)
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.actuators.accel = 1.
    _, frames = ci.apply(cc.as_reader(), self.boot)
    self.assertEqual(frames, [])

  def test_ready_or_missing_ready_evidence_returns_exact_stock_without_ecu_mutation(self):
    for ready in (True, None):
      owner, ci, _ = self.instance(ready)
      self.assertEqual(ci.CP.to_dict(), self.stock_before)
      self.assertEqual(owner.owner.phase, Phase.STOCK)
      self.assertFalse(owner.pending_target)
      self.assertEqual(self.queries, [])
      self.assertEqual(self.sent, [])
      owner.close()

  def test_pending_commit_requires_inactive_host_native_diagnostic_and_current_sources(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    for source, control in ((False, True), (True, False)):
      with self.assertRaises(RuntimeError):
        owner.commit_pending(ci, sources_current=source, control_current=control)
    self.admitted = False
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertEqual(self.queries, [])
    self.assertFalse(owner.takeover_attempted)

  def test_late_ready_before_commit_aborts_published_target_without_disable(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.boot += 10_000_000
    address, payload, bus = packer.make_can_msg('ACCELERATOR', 1, {'COUNTER': 13, 'GEAR': 5})
    raw = bytearray(payload)
    raw[3] |= 0x40
    raw[:2] = hkg_can_fd_checksum(address, None, raw).to_bytes(2, 'little')
    ci.update([(self.boot, [(address, bytes(raw), bus)])])
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertEqual(self.queries, [])
    self.assertFalse(owner.takeover_attempted)
    self.assertEqual(ci.CP.safetyConfigs[0].safetyParam, 0x5c95)

  def test_negative_or_silent_disable_restores_and_aborts_without_relabeling_cp(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.disable_ok = False
    before = ci.CP.to_dict()
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertTrue(owner.takeover_attempted)
    self.assertTrue(owner.restored)
    self.assertIn((b'\x28\x00\x01', b'\x68\x00'), self.queries)
    self.assertEqual(ci.CP.to_dict(), before)
    self.assertFalse(owner.can_emit(ci.CC))

  def test_positive_ack_with_continued_physical_scc_aborts_and_restores(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    recv = owner.callbacks[0]

    def still_sending(**kwargs):
      result = recv(**kwargs)
      result[0].append(packer.make_can_msg('SCC_CONTROL', 1, {'ACCMode': 0}))
      return result

    owner.callbacks = (still_sending, owner.callbacks[1])
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertTrue(owner.restored)
    self.assertFalse(owner.can_emit(ci.CC))
    self.assertEqual(owner.quiet_failure['address'], '0x1a0')
    self.assertEqual(ci.CP.safetyConfigs[0].safetyParam, 0x5c95)

  def test_silence_without_postack_independent_can_is_not_ownership_proof(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)

    def empty(**kwargs):
      self.seconds += .01
      self.boot += 10_000_000
      return []

    owner.callbacks = (empty, owner.callbacks[1])
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertTrue(owner.restored)
    self.assertFalse(owner.owner.active())

  def test_uncertain_restoration_stays_abort_and_cannot_claim_stock(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.disable_ok = self.restore_ok = False
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertFalse(owner.restored)
    self.assertEqual(owner.owner.phase, Phase.ABORT)
    with self.assertRaises(RuntimeError):
      owner.close()
    self.assertEqual(ci.CP.safetyConfigs[0].safetyParam, 0x5c95)

  def test_postack_sources_and_native_receipt_gate_actual_controller_then_neutral_send_handoff(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.commit(owner, ci)
    self.assertFalse(owner.sources_current(ci, self.boot))
    cc = structs.CarControl.new_message()
    _, frames = ci.apply(cc.as_reader(), self.boot)
    self.assertEqual(frames, [])
    self.warm(owner, ci, packer)
    self.assertFalse(owner.can_emit(ci.CC))
    self.acknowledge(owner, ci)
    self.assertTrue(owner.can_emit(ci.CC))
    self.assertTrue(owner.before_control(configured=True, sources_current=True, control_current=True))
    self.assertFalse(owner.before_control(configured=False, sources_current=True, control_current=True))
    self.assertFalse(owner.can_emit(ci.CC))
    self.acknowledge(owner, ci)
    owner.sent(list(self.sent), valid=True)
    self.assertFalse(owner.handed_off)
    _, frames = ci.apply(cc.as_reader(), self.boot)
    self.assertTrue({0x1A0, 0xCB, 0x100, 0x730}.issubset({frame[0] for frame in frames}))
    owner.sent(frames, valid=False)
    self.assertFalse(owner.handed_off)
    owner.sent(frames, valid=True)
    self.assertTrue(owner.handed_off)
    self.assertTrue(owner.thread.joined)
    self.admitted = True
    owner.close()
    self.assertTrue(owner.restored)

  def test_ev9_retains_its_current_mdps_estimate_and_fault_mask2(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    for tick, fault in enumerate((0, 1, 2, 4, 7), 13):
      self.boot += 10_000_000
      frame = packer.make_can_msg('MDPS', 1, {'MDPS_EstStrAnglVal': 12.3, 'MDPS_PaStrAnglVal': -45.6,
                                            'MDPS_ADAS_AciFltSig_Lv2': fault, 'COUNTER': tick})
      ci.update([(self.boot, [frame])])
      self.assertAlmostEqual(ci.CS.angle_steering_angle, 12.3)
      self.assertEqual(ci.CS.angle_steering_fault, bool(fault & 2))

  def test_first_replacement_deadline_is_software_guard_not_infinite_tester_lease(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.commit(owner, ci)
    self.seconds += 1.
    owner._tester()
    with self.assertRaises(RuntimeError):
      owner.check()
    self.assertFalse(owner.can_emit(ci.CC))

  def test_uds_time_is_included_in_first_replacement_software_bound(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    exchange = owner.exchange

    def delayed_ack(recv, send, request, response):
      result = exchange(recv, send, request, response)
      if request == b'\x28\x01\x01':
        self.seconds += 1.
      return result

    owner.exchange = delayed_ack
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertTrue(owner.restored)
    self.assertEqual(self.sent, [])
    self.assertFalse(owner.can_emit(ci.CC))

  def test_invalid_emitted_replacement_cannot_transfer_keeper_as_if_native_accepted(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.commit(owner, ci)
    self.warm(owner, ci, packer)
    self.acknowledge(owner, ci)
    _, frames = ci.apply(structs.CarControl.new_message().as_reader(), self.boot)
    bad = []
    for address, data, bus in frames:
      if address == 0x1A0:
        raw = bytearray(data)
        raw[0] ^= 1
        data = bytes(raw)
      bad.append((address, data, bus))
    owner.sent(bad, valid=True)
    self.assertFalse(owner.handed_off)
    self.assertFalse(owner.can_emit(ci.CC))
    with self.assertRaises(RuntimeError):
      owner.check()

  def test_actual_card_teardown_quiesces_and_returns_to_diagnostic_mode_before_restore(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.commit(owner, ci)
    self.admitted = False
    writes = []

    def put_bool(key, value):
      writes.append((key, value))
      self.assertTrue(owner.closed)
      self.admitted = key == 'ControlsReady' and value is False

    caller = SimpleNamespace(vehicle_startup=SimpleNamespace(owner=owner),
      params=SimpleNamespace(put_bool=put_bool), startup_diagnostic_admission=lambda: self.admitted)
    self.assertTrue(Car.prepare_pe_restoration(cast(Car, caller)))
    owner.close()
    self.assertTrue(owner.restored)
    self.assertEqual(writes, [('ControlsReady', False)])
    self.assertEqual(self.queries[-1], (b'\x10\x01', b'\x50\x01'))
    self.assertEqual(self.stock.to_dict(), self.stock_before)

  def test_diagnostic_reentry_timeout_never_sends_restore_under_tester_only_native(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.commit(owner, ci)
    self.admitted = False
    count = len(self.queries)
    timer = [0.]

    def now():
      timer[0] += .1
      return timer[0]

    writes = []
    caller = SimpleNamespace(vehicle_startup=SimpleNamespace(owner=owner),
      params=SimpleNamespace(put_bool=lambda key, value: writes.append((key, value))),
      startup_diagnostic_admission=lambda: False)
    with patch('openpilot.selfdrive.car.card.time.monotonic', side_effect=now), patch('openpilot.selfdrive.car.card.time.sleep'):
      self.assertFalse(Car.prepare_pe_restoration(cast(Car, caller)))
    with self.assertRaises(RuntimeError):
      owner.close()
    self.assertEqual(len(self.queries), count)
    self.assertFalse(owner.restored)
    self.assertEqual(writes, [('ControlsReady', False)])

  def test_stock_fallback_preserves_existing_angle_aol_marker_only(self):
    owner, ci, _ = self.instance(True, seal=False)
    ci.CP.alternativeExperience = 32
    owner.finalize_aol_configuration(ci)
    owner.seal_publication()
    self.assertTrue(owner.prepared_for(ci.CP))
    self.assertEqual(ci.CP.safetyConfigs[0].safetyParam, 0x5c91)
    self.assertEqual(self.stock.to_dict(), self.stock_before)

  def test_restore_admission_exception_is_uncertain_and_never_relabels_stock(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.commit(owner, ci)
    before = ci.CP.to_dict()
    count = len(self.queries)

    def failed_transport():
      raise OSError('Explicit diagnostic-admission fixture failure')

    owner.diagnostic_admission = failed_transport
    with self.assertRaises(RuntimeError):
      owner.close()
    self.assertTrue(owner.closed)
    self.assertFalse(owner.restored)
    self.assertEqual(owner.owner.phase, Phase.ABORT)
    self.assertEqual(len(self.queries), count)
    self.assertEqual(ci.CP.to_dict(), before)


class TestPEReadyPhysicalSource(unittest.TestCase):
  def test_missing_boottime_does_not_create_ready_false_from_defaults(self):
    source = PEReadySource()
    source.observation = (1_000_000_000, False)
    with patch('opendbc.car.hyundai.ioniq5pe_ready.clock_boot_ns', return_value=0):
      source.observe([])
    self.assertIsNone(source.current(1_000_000_000, 0, 100_000_000))

  def test_crc_counter_bus_and_freshness_require_actual_physical_sample(self):
    packer = CANPacker('hyundai_canfd_generated')
    source = PEReadySource()
    for stamp, count in ((1_010_000_000, 0), (1_020_000_000, 1)):
      message = packer.make_can_msg('ACCELERATOR', 1, {'COUNTER': count, 'GEAR': 5})
      source.update([(stamp, [message])], stamp)
    self.assertIs(source.current(1_020_000_000, 1_000_000_000, 100_000_000), False)
    self.assertIs(source.current(1_120_000_000, 1_000_000_000, 100_000_000), False)
    self.assertIsNone(source.current(1_120_000_001, 1_000_000_000, 100_000_000))
    address, data, _ = packer.make_can_msg('ACCELERATOR', 1, {'COUNTER': 2})
    wrong_bus = (address, data, 0)
    source.update([(1_030_000_000, [wrong_bus])], 1_030_000_000)
    assert source.observation is not None
    self.assertEqual(source.observation[0], 1_020_000_000)
    corrupted = bytearray(data)
    corrupted[0] ^= 1
    source.update([(1_030_000_000, [(address, bytes(corrupted), 1)])], 1_030_000_000)
    self.assertIsNone(source.current(1_030_000_000, 1_000_000_000, 100_000_000))

class TestEV9SessionAndReadyProbe(EV9StartupFixture, unittest.TestCase):
  def test_uncertain_session_alone_restores_default_without_communication_control(self):
    for outcome in (False, None, OSError('session send uncertain')):
      with self.subTest(outcome=outcome):
        owner, ci, packer = self.instance()
        self.warm(owner, ci, packer)
        before = ci.CP.to_dict()
        self.queries.clear()

        def uncertain_session(recv, send, request, response, outcome=outcome):
          self.queries.append((request, response))
          if request == b'\x10\x03':
            if isinstance(outcome, Exception):
              raise outcome
            return outcome
          return request == b'\x10\x01' and response == b'\x50\x01'

        owner.exchange = uncertain_session
        with self.assertRaises((RuntimeError, OSError)):
          self.commit(owner, ci)
        self.assertTrue(owner.session_attempted)
        self.assertFalse(owner.takeover_attempted)
        self.assertTrue(owner.restored)
        self.assertEqual(self.queries, [(b'\x10\x03', b'\x50\x03'), (b'\x10\x01', b'\x50\x01')])
        self.assertEqual(ci.CP.to_dict(), before)
        self.assertFalse(owner.can_emit(ci.CC))

  def test_session_only_uncertain_default_session_stays_unverified_and_reenters_diagnostics(self):
    owner, ci, packer = self.instance()
    self.warm(owner, ci, packer)
    self.queries.clear()
    owner.exchange = lambda recv, send, request, response: self.queries.append((request, response))
    with self.assertRaises(RuntimeError):
      self.commit(owner, ci)
    self.assertTrue(owner.session_attempted)
    self.assertFalse(owner.takeover_attempted)
    self.assertFalse(owner.restored)
    self.admitted = False
    writes = []

    def put_bool(key, value):
      writes.append((key, value))
      self.admitted = key == 'ControlsReady' and value is False

    caller = SimpleNamespace(vehicle_startup=SimpleNamespace(owner=owner),
      params=SimpleNamespace(put_bool=put_bool), startup_diagnostic_admission=lambda: self.admitted)
    self.assertTrue(Car.prepare_pe_restoration(cast(Car, caller)))
    with self.assertRaises(RuntimeError):
      owner.close()
    with self.assertRaises(RuntimeError):
      owner.close()
    self.assertFalse(owner.restored)
    self.assertEqual(writes, [('ControlsReady', False)])
    self.assertTrue(all(request in (b'\x10\x03', b'\x10\x01') for request, _ in self.queries))

  def test_live_prepare_probe_revokes_cached_false_after_later_invalid_source(self):
    for invalid in (None, 'crc', 'timestamp'):
      with self.subTest(invalid=invalid):
        owner, _, packer = self.instance()
        ticks = 0

        def recv(*, packer=packer, invalid=invalid, **kwargs):
          nonlocal ticks
          ticks += 1
          self.boot += 10_000_000
          self.seconds += .01
          frame = next(f for f in packets(packer, ticks, advance_counter=True) if f[0] == 0x35)
          if invalid == 'crc' and ticks >= 45:
            data = bytearray(frame[1])
            data[-1] ^= 1
            frame = CanData(frame[0], bytes(data), frame[2])
          stamp = 0 if invalid == 'timestamp' and ticks >= 45 else self.boot
          return [TimestampedCanPacket([frame], stamp)]

        owner.callbacks = (recv, self.sent.extend)
        with patch('opendbc.car.hyundai.ioniq5pe_startup.time.sleep'):
          result = owner._ready_probe()
        self.assertIs(result, False if invalid is None else None)
        self.assertEqual(self.queries, [])


class TestEV9NormalSelection(unittest.TestCase):
  def test_saved_choice_and_captured_ev9_factory_reach_sealed_owner_without_setting_writes(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.car_helpers import get_car
    from opendbc.car.vin import VIN_UNKNOWN
    import json
    from pathlib import Path
    FIXTURE = json.loads((Path(__file__).parent / 'testdata' / 'ev9_startup_capture.json').read_text())
    from openpilot.common.params import Params
    from openpilot.selfdrive.car.card import alpha_long_requested
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    from openpilot.starpilot.vehicle_startup import VehicleStartupOwner

    from tempfile import TemporaryDirectory
    from pathlib import Path

    class Saved:
      def __init__(self, alpha):
        self.values = {'AlwaysOnLateral': b'1', 'LongPitch': b'0'}
        if alpha is not None:
          self.values['AlphaLongitudinalEnabled'] = alpha
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        for key, value in self.values.items():
          (self.root / key).write_bytes(value)

      def get_default_value(self, key):
        return Params().get_default_value(key)

      def get_param_path(self, key):
        return str(self.root / key)

      def get(self, key, **kwargs):
        path = self.root / key
        raw = path.read_bytes() if path.exists() else None
        return raw.decode(kwargs["encoding"]) if raw is not None and kwargs.get("encoding") else raw

      def get_bool(self, key):
        return self.get(key) == b'1'

    for alpha, release in ((None, False), (b'0', False), (b'1', False), (b'1', True)):
      with self.subTest(alpha=alpha, release=release):
        saved = Saved(alpha)
        self.addCleanup(saved.directory.cleanup)
        before_files = {p.name: p.read_bytes() for p in saved.root.iterdir()}
        before = dict(saved.values)
        request = alpha_long_requested(saved, is_release=release)
        startup = VehicleStartupOwner()
        preferences = VehicleStartupPreferences.read(saved, enabled=True)
        finger = gen_empty_fingerprint()
        for bus, entries in FIXTURE['fingerprint'].items():
          finger[int(bus)].update({int(addr): length for addr, length in entries.items()})
        fw = []

        def receive(wait_for_one: bool = False) -> list[list[CanData]]:
          return []

        def before_create(cp, candidate, fingerprints, car_fw, *, preferences=preferences, startup=startup, request=request):
          cp = preferences.prepare(cp, fingerprints=fingerprints)
          return startup.prepare(cp, CarInterface, (receive, lambda frames: None),
                                 requested=request, admission=lambda: True)

        detected = (CAR.KIA_EV9, finger, VIN_UNKNOWN, fw, structs.CarParams.FingerprintSource.fw, True)
        with patch('opendbc.car.car_helpers.fingerprint', return_value=detected), \
             patch.object(PEPendingStartup, '_ready_probe', return_value=False):
          ci = get_car(receive, lambda frames: None, lambda enabled: None,
                       request, release, pre_create_hook=before_create)
        startup.configure(ci)
        startup.finalize_aol_configuration(ci)
        startup.seal_publication()
        self.assertEqual(ci.CP.openpilotLongitudinalControl, request)
        self.assertEqual(ci.CP.alphaLongitudinalAvailable, not release)
        self.assertEqual(ci.CP.safetyConfigs[0].safetyParam, 0x5c95 if request else 0x5c91)
        self.assertEqual(saved.values, before)
        self.assertEqual({p.name: p.read_bytes() for p in saved.root.iterdir()}, before_files)
        if request:
          self.assertIsInstance(startup.owner, PEPendingStartup)
          self.assertEqual(startup.owner.owner.phase, Phase.PENDING)
          self.assertTrue(startup.owner.prepared_for(ci.CP))
          self.assertTrue(startup.owner.owner.published)
        else:
          self.assertIsNone(startup.owner)


class TestEV9PendingIdentity(unittest.TestCase):
  def test_pending_owner_rejects_a_different_vehicle_stock_fallback(self):
    long = candidate(params(), enabled=True, is_release=False)
    with self.assertRaises(ValueError):
      PEPendingStartup(long, (lambda **kw: [], lambda frames: None), stock_cp=stock_params())

  def test_pe_owner_also_rejects_an_ev9_stock_fallback(self):
    from opendbc.car.hyundai.ioniq5pe_startup import PEPendingStartup as OriginalPEPendingStartup
    from opendbc.car.hyundai.ioniq5pe_long_profile import candidate as pe_candidate

    stock = stock_params()
    long = pe_candidate(stock, enabled=True, is_release=False)
    with self.assertRaises(ValueError):
      OriginalPEPendingStartup(long, (lambda wait_for_one=False, **kw: [], lambda frames: None), stock_cp=params())


class TestEV9UnpreparedController(unittest.TestCase):
  def test_current_inputs_do_not_enable_replacements_before_qualified_owner(self):
    stock = params()
    cp = candidate(stock, enabled=True, is_release=False)
    ci = CarInterface(cp)
    ci.update([])
    packer = CANPacker(DBC[CAR.KIA_EV9][Bus.pt])
    for tick in range(1, 13):
      ci.update([(tick * 10_000_000, packets(packer, tick, advance_counter=True))])
    self.assertTrue(ci.CS.out.canValid)
    control = structs.CarControl(enabled=True, latActive=True, longActive=True)
    control.actuators.accel = 1.
    _, frames = ci.apply(control.as_reader(), 130_000_000)
    self.assertEqual(frames, [])


class TestPreparedConfigurationComparison(unittest.TestCase):
  @staticmethod
  def context(car=CAR.KIA_EV9):
    cp = stock_params(candidate=car)
    owner = PEPendingStartup.__new__(PEPendingStartup)
    owner.owner = Lease(cp.copy())
    owner.owner.phase = Phase.OWNED
    owner.closed = False
    owner.fault = None
    owner.snapshot = deepcopy(cp.to_dict())
    assert owner.prepared_for(cp)
    return owner, cp

  @staticmethod
  def leaf_paths(value, path=()):
    if type(value) is dict:
      for key, child in value.items():
        yield from TestPreparedConfigurationComparison.leaf_paths(child, path + (key,))
    elif type(value) is list:
      for key, child in enumerate(value):
        yield from TestPreparedConfigurationComparison.leaf_paths(child, path + (key,))
    else:
      yield path, value

  @staticmethod
  def assign(cp, path, value):
    for key in path[:-1]:
      cp = getattr(cp, key) if type(key) is str else cp[key]
    if type(path[-1]) is str:
      setattr(cp, path[-1], value)
    else:
      cp[path[-1]] = value

  def test_every_legal_configuration_mutation_remains_freshly_rejected(self):
    checked = 0
    for car in (CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5_PE):
      for path, value in self.leaf_paths(stock_params(candidate=car).to_dict()):
        if type(value) is bool:
          changed = not value
        elif type(value) is int:
          changed = value ^ 1
        elif type(value) is float:
          changed = value + .125
        elif type(value) is str:
          changed = value + 'changed'
        elif type(value) is bytes:
          changed = value + b'changed'
        else:
          continue
        for target in ('owner', 'caller', 'snapshot'):
          owner, cp = self.context(car)
          if target == 'snapshot':
            container = owner.snapshot
            for key in path[:-1]:
              container = container[key]
            container[path[-1]] = changed
          else:
            try:
              self.assign(owner.owner.cp if target == 'owner' else cp, path, changed)
            except (ValueError, TypeError, AttributeError, RuntimeError):
              # Invalid enum/union assignments never become a configuration.
              continue
          self.assertFalse(owner.prepared_for(cp), (car, path, target))
          checked += 1
    self.assertGreater(checked, 300)

  def test_owner_fault_and_phase_are_never_cached(self):
    for phase in Phase:
      owner, cp = self.context()
      owner.owner.phase = phase
      self.assertEqual(owner.prepared_for(cp), phase in (Phase.PENDING, Phase.OWNED, Phase.STOCK))
    for name, value in (('closed', True), ('fault', 'current fault'), ('snapshot', None)):
      owner, cp = self.context()
      setattr(owner, name, value)
      self.assertFalse(owner.prepared_for(cp))

  def test_equivalent_replacements_arena_changes_and_reader_fallback(self):
    owner, cp = self.context()
    original = cp.carFingerprint
    cp.carFingerprint += 'temporary'
    cp.carFingerprint = original
    self.assertTrue(owner.prepared_for(cp))
    cp = cp.copy()
    self.assertTrue(owner.prepared_for(cp))
    owner.owner.cp = owner.owner.cp.copy()
    self.assertTrue(owner.prepared_for(cp))
    self.assertTrue(owner.prepared_for(cp.as_reader()))
    cp.mass = owner.owner.cp.mass = 0.
    owner.snapshot['mass'] = -0.
    self.assertTrue(owner.prepared_for(cp))
    cp.mass = owner.owner.cp.mass = float('nan')
    owner.snapshot['mass'] = float('nan')
    self.assertFalse(owner.prepared_for(cp))

  def test_custom_snapshot_callbacks_keep_original_semantics(self):
    trace = []

    class Meta(type):
      def __eq__(self, other):
        raise AssertionError('Metadata validation must not invoke metaclass equality')

      def __hash__(self):
        raise AssertionError('Metadata validation must not invoke metaclass hashing')

    class EqualValue(metaclass=Meta):
      def __eq__(self, other):
        trace.append(other)
        return True

    owner, cp = self.context()
    mass = owner.snapshot['mass']
    owner.snapshot['mass'] = EqualValue()
    self.assertTrue(owner.prepared_for(cp))
    self.assertEqual(trace, [mass, mass])
    self.assertIsNone(owner._prepared_cp_comparison)
