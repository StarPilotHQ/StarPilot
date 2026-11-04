import threading
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.starpilot.vehicle_startup import VehicleStartupOwner

from opendbc.car import gen_empty_fingerprint
from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import get_car
from opendbc.car.hyundai.ioniq6_handoff import (HandoffOutcome, HandoffResult, ObservingCanRecv, build_ioniq6_hda2_long_candidate,
                                                 Ioniq6StartupKeepalive, finalize_ioniq6_prepublication,
                                                 prepare_ioniq6_long_candidate, inspect_ioniq6_long_sources,
                                                 _measured_period, _read_window, query_ioniq6_adas, run_ioniq6_handoff)
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum


def refresh_host_parser_receipts(card):
  from collections import deque
  from opendbc.can.parser import MessageState

  stamp = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  parsers = {}
  for bus in (1, 2):
    source = MessageState(address=bus, name=f"source{bus}", size=8, signals=[],
                          timeout_threshold=100_000_000, timestamps=deque([stamp]))
    parsers[bus] = SimpleNamespace(message_states={bus: source})
  card.CI.can_parsers = parsers


class FakeIoniq6Bus:
  def __init__(self, *, source=True, heartbeat_source=True, heartbeat_stays_after_disable=False,
               bsm_source=True, bsm_stays_after_disable=False, missing_status=None,
               disable_works=True, disable_ack=True, restore_works=True,
               restore_ack=True, missing_required=None, post_missing_required=None):
    self.now = 0.0
    self.source = source
    self.heartbeat_source = heartbeat_source
    self.heartbeat_stays_after_disable = heartbeat_stays_after_disable
    self.bsm_source = bsm_source
    self.bsm_stays_after_disable = bsm_stays_after_disable
    self.missing_status = missing_status
    self.disable_works = disable_works
    self.disable_ack = disable_ack
    self.restore_works = restore_works
    self.restore_ack = restore_ack
    self.missing_required = missing_required
    self.post_missing_required = post_missing_required
    self.counter = 0
    self.vehicle_ready = False
    self.queries = []

  @staticmethod
  def frame(address, length, counter=0):
    data = bytearray(length)
    data[2] = counter
    data[:2] = hkg_can_fd_checksum(address, None, data).to_bytes(2, "little")
    return CanData(address, bytes(data), 1)

  def recv(self, wait_for_one=False):
    self.now = round(self.now + 0.01, 4)
    if round(self.now * 100) % 2:
      return []
    messages = [self.frame(address, length) for address, length in
                ((0x35, 32), (0x175, 24), (0xA0, 24), (0xEA, 24), (0x1CF, 8), (0x36A, 16))
                if address != self.missing_required and (self.source or address != self.post_missing_required)]
    if self.source:
      messages.append(self.frame(0x1A0, 32, self.counter))
    if self.bsm_source:
      messages.extend(self.frame(address, length, self.counter) for address, length in ((0x1BA, 24), (0x1E5, 16))
                      if address != self.missing_status)
    if self.source:
      self.counter = (self.counter + 1) % 256
    if self.heartbeat_source:
      messages.append(CanData(0x100, self.frame(0x100, 24, self.counter).dat, 0))
    from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket
    for index, message in enumerate(messages):
      if message.address == 0x35:
        data = bytearray(message.dat)
        data[2] = int(round(self.now * 50)) % 256
        if self.vehicle_ready:
          data[3] |= 0x40
        data[:2] = hkg_can_fd_checksum(0x35, None, data).to_bytes(2, 'little')
        messages[index] = CanData(0x35, bytes(data), 1)
    return [TimestampedCanPacket(messages, int(self.now * 1e9))]

  def exchange(self, request, expected):
    self.queries.append((request, expected))
    if request == b"\x10\x03":
      return True
    if request == b"\x28\x01\x01":
      if self.disable_works:
        self.source = False
        self.heartbeat_source = self.heartbeat_stays_after_disable
        self.bsm_source = self.bsm_stays_after_disable
      return self.disable_ack
    if request == b"\x28\x00\x01":
      if self.restore_works:
        self.source = True
        self.heartbeat_source = True
        self.bsm_source = True
      return self.restore_ack
    if request == b"\x10\x01":
      return self.restore_ack
    raise AssertionError(request)

  def run(self):
    return run_ioniq6_handoff(self.recv, self.exchange, lambda: self.now)


class TestIoniq6PrepublicationHandoff(unittest.TestCase):
  def test_exact_positive_ack_and_measured_silence(self):
    bus = FakeIoniq6Bus()
    result = bus.run()
    self.assertEqual(result.outcome, HandoffOutcome.CONFIRMED)
    self.assertAlmostEqual(result.source_period, 0.02, delta=0.001)
    self.assertTrue(result.disable_acknowledged)
    self.assertEqual(bus.queries, [(b"\x10\x03", b"\x50\x03"), (b"\x28\x01\x01", b"\x68\x01")])

  def test_positive_ack_without_silence_restores_before_stock(self):
    bus = FakeIoniq6Bus(disable_works=False)
    result = bus.run()
    self.assertEqual(result.outcome, HandoffOutcome.STOCK)
    self.assertTrue(result.restore_acknowledged)
    self.assertEqual(bus.queries[-2:], [(b"\x28\x00\x01", b"\x68\x00"), (b"\x10\x01", b"\x50\x01")])

  def test_unanswered_disable_can_have_taken_effect_and_must_restore(self):
    bus = FakeIoniq6Bus(disable_ack=False)
    self.assertEqual(bus.run().outcome, HandoffOutcome.STOCK)
    self.assertTrue(bus.source)

  def test_missing_pre_source_never_attempts_disable(self):
    bus = FakeIoniq6Bus(source=False)
    self.assertEqual(bus.run().outcome, HandoffOutcome.NOT_READY)
    self.assertEqual(bus.queries, [])
    bus.source = True
    self.assertEqual(bus.run().outcome, HandoffOutcome.CONFIRMED)

  def test_missing_or_persistent_stock_heartbeat_never_confirms_long(self):
    missing = FakeIoniq6Bus(heartbeat_source=False)
    self.assertEqual(missing.run().outcome, HandoffOutcome.NOT_READY)
    self.assertEqual(missing.queries, [])
    persistent = FakeIoniq6Bus(heartbeat_stays_after_disable=True)
    self.assertEqual(persistent.run().outcome, HandoffOutcome.STOCK)
    self.assertTrue(persistent.heartbeat_source)

  def test_both_stock_bsm_status_streams_must_stop_and_restore(self):
    for address in (0x1BA, 0x1E5):
      with self.subTest(missing=hex(address)):
        missing = FakeIoniq6Bus(missing_status=address)
        self.assertEqual(missing.run().outcome, HandoffOutcome.NOT_READY)
        self.assertEqual(missing.queries, [])
    persistent = FakeIoniq6Bus(bsm_stays_after_disable=True)
    self.assertEqual(persistent.run().outcome, HandoffOutcome.STOCK)
    self.assertTrue(persistent.bsm_source)
    unverified = FakeIoniq6Bus(disable_ack=False, restore_works=False)
    self.assertEqual(unverified.run().outcome, HandoffOutcome.UNAVAILABLE)

  def test_real_card_callback_batched_producer_time_needs_fresh_arrivals(self):
    from openpilot.selfdrive.car.card import can_comm_callbacks

    def make_event(counter, producer_ns):
      source = FakeIoniq6Bus.frame(0x1A0, 32, counter)
      return SimpleNamespace(logMonoTime=producer_ns,
                             can=[SimpleNamespace(address=source.address, dat=source.dat, src=source.src)])

    for old_burst_only in (False, True):
      with self.subTest(old_burst_only=old_burst_only):
        now = [0.0]
        groups = [[make_event(i, 5_000_000_000 + i * 20_000_000) for i in range(10)]] if old_burst_only else [
          [make_event(2 * j + i, 5_000_000_000 + (2 * j + i) * 20_000_000) for i in range(2)]
          for j in range(5)]

        def drain(_socket, wait_for_one=False, *, observed=now, batches=groups):
          observed[0] += 0.04
          return batches.pop(0) if batches else []

        with patch("openpilot.selfdrive.car.card.messaging.drain_sock", side_effect=drain):
          receive, _ = can_comm_callbacks(SimpleNamespace(), SimpleNamespace(send=lambda packet: None))
          samples, _, _, _, _, _ = _read_window(receive, lambda observed=now: observed[0], 0.20)
        if old_burst_only:
          self.assertIsNone(_measured_period(samples))
        else:
          self.assertAlmostEqual(_measured_period(samples), 0.02, places=6)

  def test_single_sub_eight_ms_stock_arrival_requires_new_preflight_window(self):
    # The parked Ioniq trace had continuous +1 SCC counters, but one 7.1 ms
    # producer interval in an otherwise 20 ms window. Do not lower the bound.
    deltas = (0.0169, 0.0229, 0.0170, 0.0203, 0.0294, 0.0071, 0.0216,
              0.0181, 0.0203, 0.0220, 0.0195, 0.0227, 0.0175)
    now = 0.0
    samples = [(now, 90, now)]
    for index, delta in enumerate(deltas, start=1):
      now += delta
      samples.append((now, 90 + index, now))
    self.assertIsNone(_measured_period(samples))
    clean = [(index * 0.02, 110 + index, index * 0.02) for index in range(14)]
    self.assertAlmostEqual(_measured_period(clean), 0.02)

  def test_missing_health_or_unverified_restore_is_unavailable(self):
    for options, expected in (({"missing_required": 0xEA}, HandoffOutcome.NOT_READY),
                              ({"disable_works": False, "restore_ack": False}, HandoffOutcome.UNAVAILABLE),
                              ({"post_missing_required": 0xEA, "restore_works": False}, HandoffOutcome.UNAVAILABLE)):
      with self.subTest(options=options):
        bus = FakeIoniq6Bus(**options)
        self.assertEqual(bus.run().outcome, expected)

  def test_get_car_hook_runs_before_interface_parser_and_controller_construction(self):
    fingerprint = gen_empty_fingerprint()
    fingerprint[2][0x50] = 16
    fingerprint[1][0x1CF] = 8

    def fake_fingerprint(*args, **kwargs):
      return CAR.HYUNDAI_IONIQ_6, fingerprint, "", [], 0, True

    def pre_create(cp, candidate, observed, car_fw):
      self.assertEqual(candidate, CAR.HYUNDAI_IONIQ_6)
      self.assertIs(observed, fingerprint)
      self.assertFalse(cp.openpilotLongitudinalControl)
      cp.steerAtStandstill = True
      return cp

    with patch("opendbc.car.car_helpers.fingerprint", fake_fingerprint):
      ci = get_car(lambda wait_for_one=False: [], lambda msgs: None, lambda enabled: None,
                   False, False, pre_create_hook=pre_create)
    self.assertTrue(ci.CP.steerAtStandstill)
    self.assertTrue(ci.CS.CP.steerAtStandstill)
    self.assertTrue(ci.CC.CP.steerAtStandstill)

  def test_stock_ev_optional_msla_does_not_invalidate_lateral_inputs(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus
    from opendbc.car.hyundai.values import DBC

    for model in (CAR.HYUNDAI_IONIQ_6, CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6):
      with self.subTest(model=model):
        fp = gen_empty_fingerprint()
        fp[2].update({0x50: 16, 0x2A4: 24})
        fp[1].update({0x1CF: 8, 0x35: 32, 0x130: 32})
        cp = CarInterface.get_params(model, fp, [], False, False, False)
        ci = CarInterface(cp)
        packer = CANPacker(DBC[model][Bus.pt])
        ci.update([(1_000_000_000, [])])
        sources = [(parser, state) for parser in ci.can_parsers.values()
                   for state in parser.message_states.values() if not state.ignore_alive and state.address != 0x2E0]
        for frame in range(120):
          packets = [packer.make_can_msg(state.name, parser.bus,
                                        {"COUNTER": frame % 256} if any(sig.name == "COUNTER" for sig in state.signals) else {})
                     for parser, state in sources]
          state = ci.update([(1_000_000_000 + frame * 100_000_000, packets)])
        self.assertTrue(state.canValid)
        self.assertFalse(cp.openpilotLongitudinalControl)
        self.assertTrue(cp.pcmCruise)
        self.assertFalse(cp.dashcamOnly)
        # Independent native steering inputs remain required.
        for frame in range(120, 240):
          packets = [packer.make_can_msg(msg.name, parser.bus,
                                        {"COUNTER": frame % 256} if any(sig.name == "COUNTER" for sig in msg.signals) else {})
                     for parser, msg in sources if msg.name != "MDPS"]
          state = ci.update([(1_000_000_000 + frame * 100_000_000, packets)])
        self.assertFalse(state.canValid)

  def test_card_tags_aol_candidate_before_cp_publication_without_disabling_ecu(self):
    self.assert_card_prepublication_fallback({})

  def test_card_missing_long_prerequisites_preserves_stock_lateral(self):
    for options in ({"source": False}, {"heartbeat_source": False}, {"bsm_source": False},
                    {"missing_required": 0xEA}, {"missing_status": 0x1E5}):
      with self.subTest(options=options):
        self.assert_card_prepublication_fallback(options)

  def assert_card_prepublication_fallback(self, options, *, vehicle_ready=False, alternate=False, unknown=False):
    from openpilot.common.params import Params
    from openpilot.selfdrive.car import card as card_module

    class CandidateCaptured(Exception):
      pass

    fp = gen_empty_fingerprint()
    camera_source = 0x362 if alternate else 0x2A4
    fp[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
    fp[0][0x3A5] = 24
    fp[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16, 0x36A: 16})
    bus = FakeIoniq6Bus(**options)
    bus.vehicle_ready = vehicle_ready
    expected_stock = bool(options) or vehicle_ready or unknown
    stock_word = 0x91 if alternate else 0x11
    long_word = 0x8895 if alternate else 0x8815
    stock = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fp, [], False, False, False)
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    params = Params(directory.name)
    for key in ("OpenpilotEnabledToggle", "AlphaLongitudinalEnabled"):
      params.put_bool(key, True, block=True)

    class FakeSubMaster:
      valid = {"pandaStates": True}
      alive = {"pandaStates": True}

      @staticmethod
      def update(timeout):
        pass

      def __getitem__(self, key):
        return [SimpleNamespace(ignitionLine=True, ignitionCan=False)]

    sm = FakeSubMaster()
    pm = SimpleNamespace(sock={"sendcan": object()})

    def capture_get_car(*args, pre_create_hook=None, **kwargs):
      self.assertIsNotNone(pre_create_hook)
      selected = pre_create_hook(stock, CAR.HYUNDAI_IONIQ_6, fp, [])
      self.assertEqual(selected.carFingerprint, CAR.HYUNDAI_IONIQ_6)
      self.assertFalse(selected.dashcamOnly)
      self.assertEqual(selected.safetyConfigs[-1].safetyParam, stock_word if expected_stock else long_word)
      self.assertEqual(selected.openpilotLongitudinalControl, not expected_stock)
      self.assertEqual(selected.pcmCruise, expected_stock)
      interface = CarInterface(selected)
      parser = interface.CS.get_can_parsers(selected)
      from opendbc.car import Bus
      if expected_stock:
        self.assertNotIn(camera_source, parser[Bus.cam].message_states)
        self.assertEqual(parser[Bus.pt].message_states[0x1CF].frequency, 1)
      else:
        self.assertEqual(parser[Bus.cam].message_states[camera_source].frequency, 20)
      raise CandidateCaptured

    def capture_preparation(stock_cp, long_cp, **kwargs):
      if expected_stock:
        self.assertIsNone(long_cp)
      else:
        self.assertEqual(long_cp.safetyConfigs[-1].safetyParam, long_word)
      self.assertTrue(kwargs["enabled"])
      return prepare_ioniq6_long_candidate(stock_cp, long_cp, **kwargs)

    with patch.dict("os.environ", {"AOL_REPLAY_RUNTIME": ""}), \
         patch.object(card_module, "feature_requested", side_effect=lambda _params, feature: feature == 'aol'), \
         patch.object(card_module, "prewarm_cache_contracts"), \
         patch.object(card_module.messaging, "sub_sock", return_value=object()), \
         patch.object(card_module.messaging, "SubMaster", return_value=sm), \
         patch.object(card_module.messaging, "PubMaster", return_value=pm), \
         patch.object(card_module.messaging, "recv_one_retry", return_value=SimpleNamespace(can=[1])), \
         patch.object(card_module, "Params", return_value=params), \
         patch.object(card_module, "startup_candidate", return_value=None), \
         patch.object(card_module, "get_cache", return_value=None), \
         patch.object(card_module, "can_comm_callbacks", return_value=(lambda wait_for_one=False: [], lambda msgs: None)), \
         patch.object(card_module, "read_settings", return_value=object()), \
         patch.object(card_module, "independent_axis_requested", return_value=True), \
         patch("opendbc.car.hyundai.ioniq6_handoff._ioniq6_boot_ns", side_effect=lambda: 0 if unknown else int(bus.now * 1e9)), \
         patch.object(card_module, "inspect_ioniq6_long_sources",
                      side_effect=lambda recv: inspect_ioniq6_long_sources(bus.recv, lambda: bus.now)), \
         patch.object(card_module, "prepare_ioniq6_long_candidate", side_effect=capture_preparation), \
         patch.object(card_module, "confirm_ioniq6_prepared_takeover") as confirm, \
         patch.object(card_module, "get_car", side_effect=capture_get_car):
      with self.assertRaises(CandidateCaptured):
        card_module.Car()
      confirm.assert_not_called()
      self.assertEqual(bus.queries, [])
      self.assertTrue(params.get_bool("OpenpilotEnabledToggle"))
      self.assertTrue(params.get_bool("AlphaLongitudinalEnabled"))

  def test_ready_reboot_retains_stock_before_ci_and_never_attempts_takeover(self):
    for alternate in (False, True):
      with self.subTest(alternate=alternate):
        self.assert_card_prepublication_fallback({}, vehicle_ready=True, alternate=alternate)

  def test_not_ready_cold_start_retains_existing_long_candidate(self):
    for alternate in (False, True):
      with self.subTest(alternate=alternate):
        self.assert_card_prepublication_fallback({}, vehicle_ready=False, alternate=alternate)

  def test_unknown_ready_retains_stock_before_ci_for_both_camera_topologies(self):
    for alternate in (False, True):
      with self.subTest(alternate=alternate):
        self.assert_card_prepublication_fallback({}, alternate=alternate, unknown=True)

  def test_prepublication_candidate_is_selected_only_after_complete_takeover(self):
    for bus_options, expected in (({}, HandoffOutcome.CONFIRMED),
                                  ({"disable_works": False}, HandoffOutcome.STOCK),
                                  ({"post_missing_required": 0xEA, "restore_works": False}, HandoffOutcome.UNAVAILABLE)):
      with self.subTest(bus_options=bus_options):
        bus = FakeIoniq6Bus(**bus_options)
        stock = SimpleNamespace(dashcamOnly=False)
        long = SimpleNamespace(openpilotLongitudinalControl=True,
                               safetyConfigs=[SimpleNamespace(safetyParam=0x8015)])
        with patch("opendbc.car.hyundai.ioniq6_handoff.query_ioniq6_adas",
                   side_effect=lambda recv, send, request, response, active_bus=bus: active_bus.exchange(request, response)):
          selected, prearmed, result = finalize_ioniq6_prepublication(
            stock, long, bus.recv, lambda msgs: None, enabled=True, is_release=False, clock=lambda active_bus=bus: active_bus.now)
        self.assertEqual(result.outcome, expected)
        self.assertIs(selected, long if expected is HandoffOutcome.CONFIRMED else stock)
        self.assertEqual(prearmed, expected is HandoffOutcome.CONFIRMED)
        self.assertEqual(stock.dashcamOnly, expected is HandoffOutcome.UNAVAILABLE)

  def test_preparation_selects_immutable_candidate_without_source_transaction(self):
    stock = SimpleNamespace()
    prepared = SimpleNamespace(openpilotLongitudinalControl=True,
                               safetyConfigs=[SimpleNamespace(safetyParam=0x8895)])
    selected, pending = prepare_ioniq6_long_candidate(stock, prepared, enabled=True, is_release=False)
    self.assertIs(selected, prepared)
    self.assertTrue(pending)
    self.assertEqual(prepare_ioniq6_long_candidate(stock, prepared, enabled=False, is_release=False), (stock, False))
    self.assertEqual(prepare_ioniq6_long_candidate(stock, prepared, enabled=True, is_release=True), (stock, False))
    prepared.safetyConfigs[-1].safetyParam = 0x0015
    with self.assertRaisesRegex(ValueError, 'unreviewed'):
      prepare_ioniq6_long_candidate(stock, prepared, enabled=True, is_release=False)

  def test_release_and_unreviewed_raw_do_not_request_takeover(self):
    stock = SimpleNamespace(dashcamOnly=False)
    long = SimpleNamespace(openpilotLongitudinalControl=True, safetyConfigs=[SimpleNamespace(safetyParam=0x8015)])
    bus = FakeIoniq6Bus()
    selected, armed, result = finalize_ioniq6_prepublication(
      stock, long, bus.recv, lambda msgs: None, enabled=True, is_release=True, clock=lambda: bus.now)
    self.assertIs(selected, stock)
    self.assertFalse(armed)
    self.assertIsNone(result)
    self.assertEqual(bus.queries, [])
    long.safetyConfigs[-1].safetyParam = 0x0015
    with self.assertRaises(ValueError):
      finalize_ioniq6_prepublication(stock, long, bus.recv, lambda msgs: None,
                                    enabled=True, is_release=False, clock=lambda: bus.now)

  def test_long_candidate_exact_topology_is_prepared_before_diagnostics(self):
    for steering_addr, steering_len, support_addr, support_len, raw in ((0x50, 16, 0x2A4, 24, 0x8015),
                                                                       (0x110, 32, 0x362, 32, 0x8095)):
      with self.subTest(steering_addr=steering_addr):
        fp = gen_empty_fingerprint()
        fp[2][steering_addr] = steering_len
        fp[2][support_addr] = support_len
        fp[0][0x3A5] = 24
        # The public Ioniq 6 LKA fixture contains both 0x1CF and 0x1AA; the
        # selected standard-button profile is determined by the actual 0x1CF
        # source and stock safety mode, not mere 0x1AA presence.
        fp[1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                      0x1BA: 24, 0x1E5: 16, 0x36A: 16})
        stock = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fp, [], False, False, False)
        candidate = build_ioniq6_hda2_long_candidate(stock, fp)
        self.assertIsNotNone(candidate)
        self.assertFalse(stock.openpilotLongitudinalControl)
        self.assertTrue(stock.pcmCruise)
        self.assertTrue(candidate.openpilotLongitudinalControl)
        self.assertFalse(candidate.pcmCruise)
        self.assertEqual(candidate.safetyConfigs[-1].safetyParam, raw)
        fp[2][support_addr] = 8
        self.assertIsNone(build_ioniq6_hda2_long_candidate(stock, fp))

  def test_real_isotp_adapter_keeps_interleaved_scc_evidence(self):
    pending = []

    def can_recv(wait_for_one=False):
      ret = [pending[:]] if pending else []
      pending.clear()
      return ret

    def can_send(msgs):
      for msg in msgs:
        request_len = msg.dat[0]
        request = msg.dat[1:request_len + 1]
        response = {b"\x10\x03": b"\x50\x03\x00\x32\x01\xf4", b"\x28\x01\x01": b"\x68\x01",
                    b"\x10\x01": b"\x50\x01\x00\x32\x01\xf4", b"\x28\x00\x01": b"\x68\x00"}[request]
        heartbeat = FakeIoniq6Bus.frame(0x100, 24, 9)
        pending.extend((FakeIoniq6Bus.frame(0x1A0, 32, 9), CanData(0x100, heartbeat.dat, 0),
                        CanData(0x738, bytes([len(response)]) + response + bytes(7 - len(response)), 1)))

    observed = ObservingCanRecv(can_recv)
    self.assertTrue(query_ioniq6_adas(observed, can_send, b"\x10\x03", b"\x50\x03"))
    self.assertTrue(query_ioniq6_adas(observed, can_send, b"\x28\x01\x01", b"\x68\x01"))
    self.assertTrue(query_ioniq6_adas(observed, can_send, b"\x28\x00\x01", b"\x68\x00"))
    self.assertTrue(query_ioniq6_adas(observed, can_send, b"\x10\x01", b"\x50\x01"))
    self.assertEqual(observed.source_seen_count, 4)
    self.assertEqual(observed.heartbeat_seen_count, 4)
    self.assertIsNotNone(observed.latest_source_time)

  def test_uds_response_shape_rejects_extra_communication_control_payload(self):
    pending = []

    def recv(wait_for_one=False):
      ret = [pending[:]] if pending else []
      pending.clear()
      return ret

    def send(msgs):
      pending.append(CanData(0x738, b"\x03\x68\x01\x99\x00\x00\x00\x00", 1))

    self.assertFalse(query_ioniq6_adas(recv, send, b"\x28\x01\x01", b"\x68\x01"))

  def test_keepalive_aborts_at_bounded_deadline_without_claiming_stock(self):
    now = [0.0]
    sent = []
    restored = []
    aborted = []

    def restore():
      restored.append(True)
      return False

    owner = Ioniq6StartupKeepalive(
      lambda messages: sent.extend(messages), restore, lambda: aborted.append(True),
      lambda verified: aborted.append(verified), clock=lambda: now[0], max_startup_sec=3.0)
    owner.tick()
    self.assertEqual((sent[-1].address, sent[-1].src, sent[-1].dat), (0x730, 1, b"\x02\x3e\x80\x00\x00\x00\x00\x00"))
    now[0] = 0.5
    owner.tick()
    self.assertEqual(len(sent), 2)
    now[0] = 3.0
    owner.tick()
    self.assertEqual(restored, [True])
    self.assertEqual(aborted, [True, False])
    owner.tick()
    self.assertEqual(len(sent), 2)

  def test_keepalive_stop_waits_for_inflight_send_before_controller_owner(self):
    entered = threading.Event()
    release = threading.Event()
    owner_done = threading.Event()
    sends = []

    def blocking_send(messages):
      entered.set()
      self.assertTrue(release.wait(2))
      sends.extend(messages)

    owner = Ioniq6StartupKeepalive(blocking_send, lambda: True, lambda: None, lambda verified: None)
    tick = threading.Thread(target=owner.tick)
    tick.start()
    self.assertTrue(entered.wait(2))
    stopper = threading.Thread(target=lambda: (owner.stop(), owner_done.set()))
    stopper.start()
    self.assertFalse(owner_done.wait(0.05))
    release.set()
    self.assertTrue(owner_done.wait(2))
    tick.join(2)
    stopper.join(2)
    self.assertEqual(len(sends), 1)
    owner.tick()
    self.assertEqual(len(sends), 1)

  def test_timeout_latches_loss_before_restore_and_stop_waits_for_restore(self):
    now = [0.0]
    restoring = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    lost = []

    def restore():
      self.assertEqual(lost, [True])
      restoring.set()
      self.assertTrue(release.wait(2))
      return False

    owner = Ioniq6StartupKeepalive(lambda messages: None, restore, lambda: lost.append(True),
                                   lambda verified: None, clock=lambda: now[0], max_startup_sec=1.0)
    now[0] = 1.0
    tick = threading.Thread(target=owner.tick)
    tick.start()
    self.assertTrue(restoring.wait(2))
    stopper = threading.Thread(target=lambda: (owner.stop(), stopped.set()))
    stopper.start()
    self.assertFalse(stopped.wait(0.05))
    release.set()
    self.assertTrue(stopped.wait(2))
    tick.join(2)
    stopper.join(2)
    self.assertEqual(lost, [True])

  def test_card_warmup_and_late_source_are_separate_states(self):
    from openpilot.selfdrive.car.card import Car

    card = Car.__new__(Car)
    card.ioniq6_long_prearmed = True
    card.ioniq6_host_warmed = False
    card.ioniq6_long_lost = False
    card.ioniq6_keepalive = None
    card.ioniq6_handoff_source_floor_ns = 1
    card.CI = SimpleNamespace()
    refresh_host_parser_receipts(card)
    invalid = SimpleNamespace(canValid=False)
    card.observe_ioniq6_long_authority([], invalid)
    self.assertFalse(card.ioniq6_long_lost)
    self.assertFalse(card.ioniq6_host_warmed)
    valid = SimpleNamespace(canValid=True)
    card.observe_ioniq6_long_authority([], valid)
    self.assertTrue(card.ioniq6_host_warmed)
    card.observe_ioniq6_long_authority([], invalid)
    self.assertTrue(card.ioniq6_long_lost)
    self.assertFalse(invalid.canValid)

    card.ioniq6_long_lost = False
    source = CanData(0x1A0, b"\x00" * 8, 1)
    card.observe_ioniq6_long_authority([(1_000_000_000, [source])], valid)
    self.assertTrue(card.ioniq6_long_lost)
    self.assertFalse(valid.canValid)

  @staticmethod
  def ready_card(*, ioniq_selected: bool):
    from openpilot.selfdrive.car.card import Car

    class FakeSM:
      def __init__(self):
        self.valid = {'carControl': False, 'pandaStates': True}
        self.alive = {'pandaStates': True, 'carControl': True}
        self.seen = {'carControl': True}
        self.logMonoTime = {'carControl': 1}
        self.recv_time = {'carControl': 1e-9}
        self.panda = [SimpleNamespace(safetyModel=3, safetyParam=1, controlsAllowed=False)]

      def all_alive(self, services):
        return all(self.valid[service] for service in services)

      def __getitem__(self, key):
        return self.panda if key == 'pandaStates' else None

    card = Car.__new__(Car)
    card.ci_initialized = False
    card.initialized_prev = False
    card.ioniq6_long_selected = ioniq_selected
    card.ioniq6_long_pending = ioniq_selected
    card.ioniq6_last_preflight_at = 0.0
    card.ioniq6_long_prearmed = False
    card.ioniq6_long_lost = False
    card.ioniq6_host_warmed = False
    card.ioniq6_panda_armed = False
    card.ioniq6_keepalive = None
    card.ioniq6_restore_callbacks = None
    card.ioniq6_handoff_result = None
    card.can_callbacks = (lambda wait_for_one=False: [], lambda messages: None)
    card.params = SimpleNamespace(put_bool=Mock())
    card.CP = SimpleNamespace(brand='hyundai', safetyConfigs=[object()])
    card.CI = SimpleNamespace(CC=SimpleNamespace(), CS=SimpleNamespace(), init=Mock(), apply=Mock(return_value=(object(), [])))
    card.vehicle_startup = VehicleStartupOwner()
    card.sm = FakeSM()
    card.publish_sendcan = Mock()
    card.learning_send_committed_mono_ns = 0
    card.ioniq6_panda_matches = Mock(return_value=False)
    return card

  def test_delayed_ioniq_takeover_waits_for_control_and_can_then_panda(self):
    from openpilot.selfdrive.car import card as card_module

    card = self.ready_card(ioniq_selected=True)
    cs = SimpleNamespace(canValid=True, canTimeout=False)
    cc = object()
    confirmed = HandoffResult(HandoffOutcome.CONFIRMED, 'confirmed')
    with (patch.object(card_module, 'confirm_ioniq6_prepared_takeover', return_value=confirmed) as takeover,
          patch.object(card_module.cloudlog, 'event') as logged):
      for _ in range(200):  # Delayed model/control startup cannot disable the ECU.
        card.controls_update(cs, cc)
      takeover.assert_not_called()
      card.params.put_bool.assert_not_called()
      card.CI.apply.assert_not_called()

      card.sm.valid['carControl'] = True
      cs.canValid = False
      card.controls_update(cs, cc)
      takeover.assert_not_called()
      cs.canValid = True
      card.controls_update(cs, cc)
      takeover.assert_called_once_with(*card.can_callbacks)
      self.assertEqual(logged.call_args.args, ('ioniq6_long_handoff',))
      self.assertEqual(logged.call_args.kwargs['outcome'], 'CONFIRMED')
      card.params.put_bool.assert_called_once_with('ControlsReady', True)
      card.CI.init.assert_not_called()
      card.CI.apply.assert_not_called()  # Panda is still in its prior mode.

      card.ioniq6_panda_matches.return_value = True
      card.controls_update(cs, cc)
      card.CI.apply.assert_not_called()
      self.assertFalse(card.ioniq6_host_warmed)
      cs.canValid = False  # Transaction consumed the old CAN cursor.
      card.observe_ioniq6_long_authority([], cs)
      self.assertFalse(card.ioniq6_long_lost)
      card.controls_update(cs, cc)
      card.CI.apply.assert_not_called()
      cs.canValid = True
      refresh_host_parser_receipts(card)
      card.observe_ioniq6_long_authority([], cs)
      card.controls_update(cs, cc)
      card.CI.apply.assert_called_once()
      cs.canValid = False
      card.observe_ioniq6_long_authority([], cs)
      self.assertTrue(card.ioniq6_long_lost)
      card.controls_update(cs, cc)
      card.CI.apply.assert_called_once()
      self.assertTrue(card.ioniq6_long_prearmed)
      self.assertTrue(card.ioniq6_panda_armed)

  def test_failed_delayed_takeover_never_acts_or_publishes_valid_carstate(self):
    from openpilot.selfdrive.car import card as card_module

    card = self.ready_card(ioniq_selected=True)
    card.sm.valid['carControl'] = True
    cs = SimpleNamespace(canValid=True, canTimeout=False)
    cc = object()
    failed = HandoffResult(HandoffOutcome.UNAVAILABLE, 'restore unverified')
    with (patch.object(card_module, 'confirm_ioniq6_prepared_takeover', return_value=failed) as takeover,
          patch.object(card_module.cloudlog, 'event') as logged):
      card.controls_update(cs, cc)
      self.assertFalse(cs.canValid)
      card.params.put_bool.assert_not_called()
      card.CI.apply.assert_not_called()
      for _ in range(10):
        cs.canValid = True  # The next raw CAN frame is valid but CP is still LONG.
        card.observe_ioniq6_long_authority([], cs)
        self.assertFalse(cs.canValid)
        card.controls_update(cs, cc)
      takeover.assert_called_once()
      self.assertEqual(logged.call_args.kwargs['reason'], 'restore unverified')
      card.CI.apply.assert_not_called()

  def test_pending_takeover_requires_live_diagnostic_panda_and_neutral_controls(self):
    from openpilot.selfdrive.car import card as card_module

    card = self.ready_card(ioniq_selected=True)
    card.sm.valid['carControl'] = True
    cs = SimpleNamespace(canValid=True, canTimeout=False)
    cc = object()
    confirmed = HandoffResult(HandoffOutcome.CONFIRMED, 'confirmed')
    with patch.object(card_module, 'confirm_ioniq6_prepared_takeover', return_value=confirmed) as takeover:
      for mutation in (
        lambda: card.sm.valid.__setitem__('pandaStates', False),
        lambda: card.sm.alive.__setitem__('pandaStates', False),
        lambda: setattr(card.sm.panda[0], 'safetyModel', 28),
        lambda: setattr(card.sm.panda[0], 'safetyParam', 2),
        lambda: setattr(card.sm.panda[0], 'controlsAllowed', True),
        lambda: card.sm.panda.append(SimpleNamespace(safetyModel=3, safetyParam=1, controlsAllowed=False)),
      ):
        card.sm.valid['pandaStates'] = True
        card.sm.alive['pandaStates'] = True
        card.sm.panda[:] = [SimpleNamespace(safetyModel=3, safetyParam=1, controlsAllowed=False)]
        mutation()
        card.controls_update(cs, cc)
        takeover.assert_not_called()
        self.assertTrue(card.ioniq6_long_pending)
        card.params.put_bool.assert_not_called()
        card.CI.apply.assert_not_called()

      for diagnostic_param in (0, 1):
        next_card = self.ready_card(ioniq_selected=True)
        next_card.sm.valid['carControl'] = True
        next_card.sm.panda[0].safetyParam = diagnostic_param
        next_card.controls_update(SimpleNamespace(canValid=True, canTimeout=False), cc)
        self.assertTrue(next_card.ioniq6_long_prearmed)
        next_card.params.put_bool.assert_called_once_with('ControlsReady', True)
      self.assertEqual(takeover.call_count, 2)

  def test_preflight_miss_retries_without_uds_or_permanent_latch(self):
    from openpilot.selfdrive.car import card as card_module

    card = self.ready_card(ioniq_selected=True)
    card.sm.valid['carControl'] = True
    cs = SimpleNamespace(canValid=True, canTimeout=False)
    cc = object()
    clock = [100.0]
    waiting = HandoffResult(HandoffOutcome.NOT_READY, 'preflight_scc_period')
    confirmed = HandoffResult(HandoffOutcome.CONFIRMED, 'confirmed')
    with (patch.object(card_module, 'confirm_ioniq6_prepared_takeover', side_effect=(waiting, confirmed)) as takeover,
          patch.object(card_module.time, 'monotonic', side_effect=lambda: clock[0])):
      card.controls_update(cs, cc)
      self.assertTrue(card.ioniq6_long_pending)
      self.assertFalse(card.ioniq6_long_lost)
      self.assertTrue(cs.canValid)
      card.params.put_bool.assert_not_called()
      card.CI.apply.assert_not_called()
      self.assertEqual(takeover.call_count, 1)

      clock[0] += 0.2
      card.controls_update(cs, cc)
      self.assertEqual(takeover.call_count, 1)
      clock[0] += 0.4
      card.controls_update(cs, cc)
      self.assertEqual(takeover.call_count, 2)
      self.assertFalse(card.ioniq6_long_pending)
      self.assertTrue(card.ioniq6_long_prearmed)
      card.params.put_bool.assert_called_once_with('ControlsReady', True)
      card.CI.apply.assert_not_called()  # Hyundai safety has not switched yet.

  def test_generic_ecu_init_waits_for_valid_can_and_live_control_once(self):
    card = self.ready_card(ioniq_selected=False)
    cs = SimpleNamespace(canValid=True, canTimeout=False)
    cc = object()
    for _ in range(20):
      card.controls_update(cs, cc)
    card.CI.init.assert_not_called()
    card.sm.valid['carControl'] = True
    cs.canValid = False
    card.controls_update(cs, cc)
    card.CI.init.assert_not_called()
    cs.canValid = True
    card.controls_update(cs, cc)
    card.controls_update(cs, cc)
    card.CI.init.assert_called_once_with(card.CP, *card.can_callbacks)
    card.params.put_bool.assert_called_once_with('ControlsReady', True)
    self.assertEqual(card.CI.apply.call_count, 2)

  def test_card_late_source_reads_actual_can_converter_tuples(self):
    from openpilot.selfdrive.car.card import Car
    from openpilot.selfdrive.pandad import can_capnp_to_list, can_list_to_can_capnp

    card = Car.__new__(Car)
    card.ioniq6_long_prearmed = True
    card.ioniq6_host_warmed = True
    card.ioniq6_long_lost = False
    card.ioniq6_keepalive = None
    valid = SimpleNamespace(canValid=True)

    def observe(frames):
      packets = can_capnp_to_list([can_list_to_can_capnp(frames)])
      self.assertIs(type(packets[0][1][0]), tuple)
      card.observe_ioniq6_long_authority(packets, valid)

    observe([CanData(0x35, b'\0' * 32, 1), CanData(0x1A0, b'\0' * 32, 0)])
    self.assertFalse(card.ioniq6_long_lost)  # The SCC address on the wrong bus is not a source.
    observe([CanData(0x1A0, b'\0' * 32, 1)])
    self.assertTrue(card.ioniq6_long_lost)
    self.assertFalse(valid.canValid)

    card.ioniq6_long_lost = False
    valid.canValid = True
    heartbeat = CanData(0x100, b"\x00" * 8, 0)
    card.observe_ioniq6_long_authority([(1_000_000_000, [heartbeat])], valid)
    self.assertTrue(card.ioniq6_long_lost)
    self.assertFalse(valid.canValid)

  def test_card_serialized_receive_publish_send_and_source_revocation(self):
    from opendbc.car import structs
    from opendbc.car.structs import car
    from openpilot.cereal import messaging
    from openpilot.selfdrive.car import card as card_module
    from openpilot.selfdrive.pandad import can_capnp_to_list, can_list_to_can_capnp

    card_owner = card_module.Car.__new__(card_module.Car)
    cp = car.CarParams(openpilotLongitudinalControl=True, pcmCruise=False, passive=False)
    cp.init('safetyConfigs', 1)
    card_owner.CP = cp
    card_owner.vehicle_startup = VehicleStartupOwner()
    card_owner.ci_initialized = False
    from openpilot.starpilot.gps.publisher import CarGpsPublisher
    card_owner.car_gps_publisher = CarGpsPublisher()
    card_owner.initialized_prev = False
    card_owner.ioniq6_long_selected = True
    card_owner.ioniq6_long_pending = True
    card_owner.ioniq6_last_preflight_at = 0.0
    card_owner.ioniq6_long_prearmed = False
    card_owner.ioniq6_long_lost = False
    card_owner.ioniq6_host_warmed = False
    card_owner.ioniq6_panda_armed = False
    card_owner.ioniq6_keepalive = None
    card_owner.ioniq6_restore_callbacks = None
    card_owner.ioniq6_restore_attempted = False
    card_owner.can_rcv_cum_timeout_counter = 0
    card_owner.can_sock = object()
    card_owner.can_callbacks = (lambda wait_for_one=False: [], lambda frames: None)
    card_owner.params = SimpleNamespace(put_bool=Mock())
    card_owner.slc_replay = card_owner.curve_replay = card_owner.conditional_replay = card_owner.aol_replay = False
    card_owner.slc_command_sock = None
    card_owner.aol_card_intent = None
    card_owner.wheel_publisher = SimpleNamespace(observe=Mock(return_value=()), publish=Mock(), suppress_distance_release=False)
    card_owner.wheel_commands = ()
    card_owner.ioniq6_media = None
    card_owner.switchback_button_tracker = None
    card_owner.switchback_settings_owner = None
    card_owner.CC_prev = car.CarControl()
    card_owner.CS_prev = car.CarState()
    card_owner.last_actuators_output = structs.CarControl.Actuators()
    card_owner.rk = SimpleNamespace(remaining=0.0)
    card_owner.is_metric = True
    card_owner.experimental_mode = False
    card_owner.initial_cruise_speed = 40
    card_owner.v_cruise_helper = SimpleNamespace(update_v_cruise=lambda *_, slc_enabled=False, slc_approved=None: None,
                                                 initialize_v_cruise=lambda *_, **kwargs: None, slc_consumed_button=None, toyota=None,
                                                 slc_cruise_change=None, v_cruise_kph=60.0, v_cruise_cluster_kph=60.0)
    card_owner.RI = SimpleNamespace(update=lambda _: None)
    control = car.CarControl(enabled=True, longActive=True)

    class SM:
      frame = 1
      seen = {'onroadEvents': True, 'carControl': True}
      valid = {'carControl': True}
      alive = {'carControl': True}
      logMonoTime = {'carControl': 1, 'onroadEvents': 0}
      recv_time = {'carControl': 1e-9}

      def update(self, _timeout):
        pass

      def __getitem__(self, name):
        if name == 'deviceState':
          return SimpleNamespace(startedMonoTime=1)
        return [] if name == 'onroadEvents' else control

      def all_alive(self, names):
        return all(self.alive[name] for name in names)

      def all_checks(self, names):
        return all(self.valid[name] for name in names)

    card_owner.sm = SM()
    sent = []
    card_owner.pm = SimpleNamespace(send=lambda service, packet: sent.append((service, packet)))
    card_owner.CI = SimpleNamespace(CP=cp, CC=SimpleNamespace(), CS=SimpleNamespace(),
                                    update=Mock(side_effect=lambda packets: car.CarState(canValid=True, canTimeout=False)),
                                    apply=Mock(return_value=(structs.CarControl.Actuators(), [CanData(0x1A0, b'\0' * 8, 1)])),
                                    init=Mock())

    def parsed_update(_packets):
      refresh_host_parser_receipts(card_owner)
      return car.CarState(canValid=True, canTimeout=False)

    card_owner.CI.update.side_effect = parsed_update
    card_owner.ioniq6_panda_diagnostic_ready = Mock(return_value=True)
    card_owner.ioniq6_panda_matches = Mock(side_effect=(False, True, True))
    packets = iter((
      can_list_to_can_capnp([CanData(0x35, b'\0' * 32, 1)]),
      can_list_to_can_capnp([CanData(0x35, b'\0' * 32, 1)]),
      can_list_to_can_capnp([CanData(0x1A0, b'\0' * 32, 1)]),
    ))
    with (patch.object(card_module.messaging, 'drain_sock_raw', side_effect=lambda *_args, **_kwargs: [next(packets)]),
          patch.object(card_module, 'confirm_ioniq6_prepared_takeover', return_value=HandoffResult(HandoffOutcome.CONFIRMED, 'confirmed')),
          patch.object(card_module.cloudlog, 'event')):
      card_owner.step()
      card_owner.step()
      card_owner.step()
    card_owner.CI.init.assert_not_called()
    self.assertEqual(card_owner.CI.apply.call_count, 1)
    self.assertEqual(card_owner.CI.update.call_count, 3)
    self.assertTrue(all(type(group[0][1][0]) is tuple for (group,) in (call.args for call in card_owner.CI.update.call_args_list)))
    outputs = [(messaging.log_from_bytes(packet).valid, can_capnp_to_list([packet], msgtype='sendcan')[0][1])
               for service, packet in sent if service == 'sendcan']
    self.assertEqual([(valid, [frame[0] for frame in frames]) for valid, frames in outputs],
                     [(False, []), (True, [0x1A0]), (False, [])])
    published = [packet for service, packet in sent if service == 'carState']
    self.assertEqual([message.valid for message in published], [True, True, False])
    self.assertTrue(card_owner.ioniq6_long_lost)
    original_car = card_module.Car
    original_new = original_car.__dict__.get('__new__')
    constructor = SimpleNamespace(__new__=lambda _cls: card_owner, __init__=lambda _owner: None)
    with (patch.object(card_module, 'config_realtime_process'),
          patch.object(card_module, 'Car', constructor),
          patch.object(card_owner, 'card_thread', return_value=None),
          patch.object(card_module, 'restore_ioniq6_adas', return_value=True) as restore):
      card_module.main()
    restore.assert_called_once_with(*card_owner.can_callbacks)
    self.assertIs(card_module.Car, original_car)
    self.assertIs(original_car.__dict__.get("__new__"), original_new)
    self.assertIsInstance(object.__new__(original_car), original_car)

  def test_card_requires_fresh_matching_panda_mode(self):
    from openpilot.selfdrive.car.card import Car

    class PandaSM:
      def __init__(self):
        self.valid = {"pandaStates": True}
        self.alive = {"pandaStates": True}
        self.states = [SimpleNamespace(safetyModel=28, safetyParam=0x8015,
                                       alternativeExperience=0, safetyRxChecksInvalid=False)]

      def __getitem__(self, key):
        return self.states

    card = Car.__new__(Car)
    card.sm = PandaSM()
    card.CP = SimpleNamespace(safetyConfigs=[SimpleNamespace(safetyModel=28, safetyParam=0x8015)],
                              alternativeExperience=0)
    self.assertTrue(card.ioniq6_panda_matches())
    card.sm.alive["pandaStates"] = False
    self.assertFalse(card.ioniq6_panda_matches())
    card.sm.alive["pandaStates"] = True
    card.sm.states[0].safetyParam = 0x11
    self.assertFalse(card.ioniq6_panda_matches())

  def test_main_stops_diagnostic_worker_after_partial_constructor_failure(self):
    from openpilot.selfdrive.car import card as card_module

    stopped = []

    def fail_after_prearm(card):
      card.ioniq6_keepalive = SimpleNamespace(stop=lambda: stopped.append(True))
      card.ioniq6_long_lost = False
      card.ioniq6_long_prearmed = True
      card.ioniq6_init_complete = False
      card.ioniq6_restore_callbacks = (lambda wait_for_one=False: [], lambda messages: None)
      raise RuntimeError("failed after ECU handoff")

    with (patch.object(card_module, "config_realtime_process"), patch.object(card_module.Car, "__init__", fail_after_prearm),
          patch.object(card_module, "restore_ioniq6_adas", return_value=True) as restore):
      with self.assertRaisesRegex(RuntimeError, "failed after ECU handoff"):
        card_module.main()
    self.assertEqual(stopped, [True])
    restore.assert_called_once()

  def test_main_restores_after_post_init_exception_and_normal_shutdown(self):
    from openpilot.selfdrive.car import card as card_module

    def check_case(outcome, restored):
      cards = []
      stopped = []
      callbacks = (lambda wait_for_one=False: [], lambda messages: None)

      def prepared(card):
        cards.append(card)
        card.ioniq6_keepalive = SimpleNamespace(stop=lambda: stopped.append(True))
        card.ioniq6_long_lost = False
        card.ioniq6_long_prearmed = True
        card.ioniq6_init_complete = True
        card.ioniq6_restore_callbacks = callbacks
        card.ioniq6_restore_attempted = False

      def run(_card):
        if outcome == 'crash':
          raise RuntimeError('failed after CP publication')

      with (patch.object(card_module, 'config_realtime_process'), patch.object(card_module.Car, '__init__', prepared),
            patch.object(card_module.Car, 'card_thread', run),
            patch.object(card_module, 'restore_ioniq6_adas', return_value=restored) as restore,
            patch.object(card_module.cloudlog, 'error') as logged):
        if outcome == 'crash':
          with self.assertRaisesRegex(RuntimeError, 'failed after CP publication'):
            card_module.main()
        else:
          card_module.main()
      self.assertTrue(cards[0].ioniq6_long_lost)
      self.assertTrue(cards[0].ioniq6_restore_attempted)
      self.assertEqual(stopped, [True])
      restore.assert_called_once_with(*callbacks)
      if restored:
        logged.assert_not_called()
      else:
        logged.assert_called_once_with('Ioniq 6 Card exited; stock SCC restoration unverified')

    for outcome, restored in (('crash', True), ('shutdown', True), ('crash', False)):
      with self.subTest(outcome=outcome, restored=restored):
        check_case(outcome, restored)

  def test_main_does_not_repeat_keepalive_restore(self):
    from openpilot.selfdrive.car import card as card_module

    def prepared(card):
      card.ioniq6_keepalive = SimpleNamespace(stop=lambda: None)
      card.ioniq6_long_lost = False
      card.ioniq6_long_prearmed = True
      card.ioniq6_restore_callbacks = (lambda wait_for_one=False: [], lambda messages: None)
      card.ioniq6_restore_attempted = True  # Timeout worker already restored or tried to restore.

    with (patch.object(card_module, 'config_realtime_process'), patch.object(card_module.Car, '__init__', prepared),
          patch.object(card_module.Car, 'card_thread', lambda _card: None),
          patch.object(card_module, 'restore_ioniq6_adas') as restore):
      card_module.main()
    restore.assert_not_called()

  def test_same_card_can_cursor_consumes_pre_disable_scc_before_state_update(self):
    from openpilot.selfdrive.car.card import can_comm_callbacks

    bus = FakeIoniq6Bus()
    bus.counter = 1
    pending = [[FakeIoniq6Bus.frame(0x1A0, 32, 0)]]

    def drain(_socket, wait_for_one=False):
      if pending:
        packet = pending.pop(0)
        bus.now += 0.02
        frames = [packet]
      else:
        frames = bus.recv(wait_for_one)
      return [SimpleNamespace(logMonoTime=int(bus.now * 1e9),
                              can=[SimpleNamespace(address=m.address, dat=m.dat, src=m.src) for m in packet])
              for packet in frames]

    with patch("openpilot.selfdrive.car.card.messaging.drain_sock", side_effect=drain):
      receive, _ = can_comm_callbacks(SimpleNamespace(), SimpleNamespace(send=lambda packet: None))
      result = run_ioniq6_handoff(receive, bus.exchange, lambda: bus.now)
      self.assertEqual(result.outcome, HandoffOutcome.CONFIRMED)
      self.assertEqual(pending, [])
      self.assertFalse(any(m.address == 0x1A0 for packet in receive() for m in packet))
