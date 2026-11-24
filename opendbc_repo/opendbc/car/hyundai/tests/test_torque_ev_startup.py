"""Exact typed CP and transaction outcome contracts, without vehicle transport."""

import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from opendbc.car import gen_empty_fingerprint, make_tester_present_msg
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.ecu_startup import Outcome
from opendbc.car.hyundai.torque_ev_startup import candidate, required, TorqueEVStartup


@pytest.fixture(autouse=True)
def boottime_clock():
  with patch.object(time, "CLOCK_BOOTTIME", getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC), create=True):
    yield


@pytest.fixture(params=[(CAR.HYUNDAI_IONIQ_5, False), (CAR.HYUNDAI_IONIQ_5, True), (CAR.HYUNDAI_KONA_EV_2ND_GEN, False), (CAR.HYUNDAI_KONA_EV_2ND_GEN, True)])
def stock(request):
  car, alt = request.param
  fp = gen_empty_fingerprint()
  fp[2].update({0x110: 32, 0x362: 32} if alt else {0x50: 16, 0x2A4: 24})
  fp[1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  return CarInterface.get_params(car, fp, [], False, False, False)


class Keeper:
  def __init__(self, *args, **kwargs):
    self.abort_reason = None
    self.started = False

  def start(self):
    self.started = True

  def stop(self):
    self.started = False


def owner(stock, *, session=True, reply=None, restore=True, send_exception=False):
  sent = []

  def send(frames):
    sent.extend(frames)
    if send_exception and any(data[:4] == b"\x03\x28\x83\x01" for _, data, _ in frames):
      raise OSError("after emission")

  instance = TorqueEVStartup(candidate(stock, requested=True, is_release=False), (list, send), stock_cp=stock, keeper_factory=Keeper)

  def query(sender, receiver, bus, addresses, requests, responses):
    request = requests[0]

    def get_data(timeout):
      sender([(instance.address, bytes((len(request),)) + request + bytes(7 - len(request)), bus)])
      if request == b"\x10\x03":
        return {(instance.address, None): b""} if session else {}
      if request == b"\x28\x83\x01":
        return {} if reply is None else {(instance.address, None): reply}
      if request == b"\x28\x00\x01":
        return {(instance.address, None): b""} if restore else {}
      if request == b"\x10\x01":
        return {(instance.address, None): b""} if restore else {}
      raise AssertionError(request)

    return SimpleNamespace(get_data=get_data)

  instance._query = query
  return instance, sent


def test_exact_typed_cp_promotes_without_stock_aol_alias(stock):
  cp = candidate(stock, requested=True, is_release=False)
  assert cp is not None
  assert cp.safetyConfigs[0].safetyParam in (0x15, 0x95)
  assert required(cp)
  assert not required(stock)
  assert candidate(stock, requested=False, is_release=False) is None
  assert candidate(stock, requested=True, is_release=True) is None
  cp.safetyConfigs[0].safetyParam = 0
  assert required(cp)  # malformed LONG still requires sealed owner


def test_session_failure_restores_default_without_disable_and_retains_entire_stock(stock):
  startup, sent = owner(stock, session=False)
  with patch("opendbc.car.hyundai.torque_ev_startup.time.sleep"):
    final = startup.prepare(admission=lambda: True)
  assert startup.session_attempted and not startup.disable_attempted
  assert all(data[1:2] == b"\x10" for _, data, _ in sent)
  assert sent[-1][1][:3] == b"\x02\x10\x01"
  assert startup.outcome is Outcome.STOCK_RESTORED
  assert final.to_dict() == stock.to_dict()


def test_suppressed_silence_is_sent_unconfirmed_not_positive_ack(stock):
  startup, sent = owner(stock)
  with patch("opendbc.car.hyundai.torque_ev_startup.time.sleep"):
    final = startup.prepare(admission=lambda: True)
  assert startup.outcome is Outcome.SENT_UNCONFIRMED
  assert startup.disable_response == "suppressed_reply_sent_unconfirmed"
  assert final.openpilotLongitudinalControl
  assert [data[1:4] for _, data, _ in sent] == [b"\x10\x03\x00", b"\x28\x83\x01"]
  assert startup.keeper.started
  assert not startup.before_control(configured=False, sources_current=True, control_current=True)
  assert startup.before_control(configured=True, sources_current=True, control_current=True)
  assert startup.keeper.started
  startup.configure(SimpleNamespace(CP=final))
  startup.seal_publication()
  assert startup.published
  startup.sent([make_tester_present_msg(0x730, 1, suppress_response=True)], valid=True)
  assert not startup.keeper.started and startup.handed_off
  startup.close()


def test_negative_or_exception_after_emission_restores_exact_stock(stock):
  for kwargs in ({"reply": b"\x7f\x28\x22"}, {"send_exception": True}):
    startup, sent = owner(stock, **kwargs)
    with patch("opendbc.car.hyundai.torque_ev_startup.time.sleep"):
      final = startup.prepare(admission=lambda: True)
    assert sent
    assert startup.disable_attempted and startup.restored
    assert startup.outcome is Outcome.STOCK_RESTORED
    assert final.to_dict() == stock.to_dict()


def test_unconfirmed_restore_aborts_before_publication(stock):
  startup, _ = owner(stock, reply=b"\x7f\x28\x22", restore=False)
  with patch("opendbc.car.hyundai.torque_ev_startup.time.sleep"), pytest.raises(RuntimeError):
    startup.prepare(admission=lambda: True)
  assert startup.outcome is Outcome.ABORT_UNCERTAIN
  assert not startup.published


def test_actual_factory_and_malformed_long_cannot_bypass_owner(stock):
  from openpilot.starpilot.vehicle_startup import VehicleStartupOwner

  assert CarInterface.startup_owner(stock, (list, lambda frames: None), requested=False) is None
  startup = CarInterface.startup_owner(stock, (list, lambda frames: None), requested=True)
  assert isinstance(startup, TorqueEVStartup)
  malformed = candidate(stock, requested=True, is_release=False)
  malformed.safetyConfigs[0].safetyParam = 0
  ci = CarInterface(malformed)
  with pytest.raises(RuntimeError, match="matching prepared startup owner"):
    VehicleStartupOwner().configure(ci)


def test_real_stock_parser_warmup_before_publication(stock):
  import time
  from opendbc.can.packer import CANPacker
  from opendbc.car import Bus
  from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket

  startup, sent = owner(stock, reply=b"\x7f\x28\x22")
  with patch("opendbc.car.hyundai.torque_ev_startup.time.sleep"):
    cp = startup.prepare(admission=lambda: True)
  ci = CarInterface(cp)
  ci.update([])
  packers = {bus: CANPacker(parser.dbc.name) for bus, parser in ci.can_parsers.items()}
  tick = 0

  def recv():
    nonlocal tick
    tick += 1
    frames = physical_frames(ci.CP, packers[Bus.pt], tick)
    return [TimestampedCanPacket(frames, time.clock_gettime_ns(time.CLOCK_BOOTTIME))]

  startup.callbacks = (recv, sent.extend)
  with patch.object(time, "CLOCK_BOOTTIME", getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC), create=True):
    startup.floor_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    startup.configure(ci)
    startup.seal_publication()
  assert startup.ready and startup.published
  assert ci.CP.to_dict() == stock.to_dict()
  assert ci.can_parsers[Bus.pt].can_valid


@pytest.mark.parametrize("car", [CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_KONA_EV_2ND_GEN])
@pytest.mark.parametrize("variant", ["hda1", "alt_buttons", "multi_board"])
@pytest.mark.parametrize("alpha", [False, True])
def test_unowned_topologies_preserve_factory_output(car, variant, alpha):
  fp = gen_empty_fingerprint()
  if variant == "multi_board":
    bus = 6 if variant == "multi_board" else 2
    fp[bus][0x50] = 16
  if variant != "alt_buttons":
    fp[5 if variant == "multi_board" else 1][0x1CF] = 8
  # Disable only the new intercept to obtain the retained upstream factory path.
  with patch("opendbc.car.hyundai.torque_ev_startup.topology_owned", return_value=False):
    retained = CarInterface.get_params(car, fp, [], alpha, False, False)
  actual = CarInterface.get_params(car, fp, [], alpha, False, False)
  if variant == "alt_buttons":
    assert actual.flags & HyundaiFlags.CANFD_ALT_BUTTONS
  assert actual.to_dict() == retained.to_dict()
  assert candidate(actual, requested=True, is_release=False) is None
  assert not required(actual)


@pytest.mark.parametrize("reply,accepted", [(None, True), (b"\x68\x03", True), (b"\x7f\x28\x22", False), (b"\x68\x01", False)])
def test_real_isotp_decoder_classifies_suppressed_command(stock, reply, accepted):
  import time
  from opendbc.car import CanData
  from opendbc.car.isotp_parallel_query import IsoTpParallelQuery

  pending = []
  requests = []
  ticks = 0

  def clock():
    nonlocal ticks
    ticks += 1
    return ticks * 0.001

  def send(frames):
    for address, data, bus in frames:
      assert address == 0x730 and bus == 1
      request = bytes(data[1 : 1 + data[0]])
      requests.append(request)
      payload = b"\x50\x03" if request == b"\x10\x03" else reply
      if payload is not None:
        pending.append(CanData(0x738, bytes([len(payload)]) + payload + bytes(7 - len(payload)), 1))

  def recv(wait_for_one=False):
    packets = [list(pending)] if pending else []
    pending.clear()
    return packets

  startup = TorqueEVStartup(candidate(stock, requested=True, is_release=False), (recv, send), stock_cp=stock, keeper_factory=Keeper)
  startup._query = IsoTpParallelQuery
  with patch.object(time, "monotonic", side_effect=clock), patch.object(time, "sleep"):
    result = startup._disable(recv, startup._send)
  assert result is accepted
  assert requests == [b"\x10\x03", b"\x28\x83\x01"]
  assert startup.disable_attempted


def physical_frames(cp, packer, tick):
  """Independent physical inventory from update_canfd reads and source bus topology."""
  from opendbc.car import CanData

  sources = (
    ("ACCELERATOR", 1, {"GEAR": 5, "ACCELERATOR_PEDAL": 0}),
    ("TCS", 1, {"ACCEnable": 0, "ACC_REQ": 0, "DriverBraking": 0}),
    ("WHEEL_SPEEDS", 1, dict.fromkeys(("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal"), 30)),
    ("MDPS", 1, {"MDPS_StrTqSnsrVal": 0, "MDPS_OutTqVal": 0}),
    ("STEERING_SENSORS", 1, {}),
    ("DOORS_SEATBELTS", 1, {"DRIVER_SEATBELT": 1}),
    ("BLINKERS", 1, {}),
    ("ADAS_CMD_50_50ms", 1, {}),
    ("MANUAL_SPEED_LIMIT_ASSIST", 1, {}),
    ("CRUISE_BUTTONS", 1, {"COUNTER": tick % 16}),
    ("CRUISE_BUTTONS_ALT", 1, {"COUNTER": tick % 256}),
    ("CAM_0x362" if cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "CAM_0x2a4", 2, {}),
    ("FR_CMR_02_100ms", 1, {}),
  )
  # The removed ADAS ECU must not supply SCC to the LONG health proof.
  if not cp.openpilotLongitudinalControl:
    sources += (("SCC_CONTROL", 1, {"MainMode_ACC": 1, "ACCMode": 0}),)
  result = []
  for name, bus, values in sources:
    signals = packer.dbc.name_to_msg[name].sigs
    assert set(values) <= set(signals), (name, values)
    values = dict(values)
    if "COUNTER" in signals:
      values["COUNTER"] = tick % (1 << signals["COUNTER"].size)
    result.append(CanData(*packer.make_can_msg(name, bus, values)))
  return result


@pytest.mark.parametrize("alpha", (False, True))
@pytest.mark.parametrize("release", (False, True))
def test_factory_keeps_stock_until_prepared_saved_choice(stock, alpha, release):
  fp = gen_empty_fingerprint()
  alt = bool(stock.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT)
  fp[2].update({0x110: 32, 0x362: 32} if alt else {0x50: 16, 0x2A4: 24})
  fp[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  cp = CarInterface.get_params(stock.carFingerprint, fp, [], alpha, release, False)
  assert not cp.openpilotLongitudinalControl and cp.pcmCruise
  assert cp.alphaLongitudinalAvailable == (not release)
  assert cp.safetyConfigs[0].safetyParam == (0x91 if alt else 0x11)
  promoted = candidate(cp, requested=alpha, is_release=release)
  assert (promoted is not None) == (alpha and not release)


def test_no_unprepared_long_constructor_publication(stock):
  from openpilot.starpilot.vehicle_startup import VehicleStartupOwner

  cp = candidate(stock, requested=True, is_release=False)
  for mutation in (lambda c: setattr(c.safetyConfigs[0], "safetyParam", 0), lambda c: setattr(c, "steerActuatorDelay", c.steerActuatorDelay + 0.1)):
    bad = cp.as_reader().as_builder()
    mutation(bad)
    with pytest.raises(RuntimeError, match="matching prepared startup owner"):
      VehicleStartupOwner().configure(CarInterface(bad))


@pytest.mark.parametrize("mode", ("sent", "untouched", "restored", "uncertain"))
@pytest.mark.parametrize("aol", (False, True))
def test_actual_card_publishes_only_sealed_cp_then_controls(stock, mode, aol, monkeypatch, tmp_path):
  import gc
  from opendbc.car import Bus
  from opendbc.can import CANPacker
  from opendbc.car import car_helpers, structs
  from opendbc.car.hyundai.values import DBC
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

  monkeypatch.setenv("PARAMS_ROOT", str(tmp_path / "params"))
  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("REPLAY", "1")
  monkeypatch.setenv("AOL_REPLAY_RUNTIME", "0")
  with OpenpilotPrefix():
    saved = Params()
    for key, value in (("OpenpilotEnabledToggle", True), ("AlphaLongitudinalEnabled", True), ("IsReleaseBranch", False), ("AlwaysOnLateral", aol)):
      saved.put_bool(key, value, block=True)
    live_ci = []
    holder = VehicleStartupOwner()
    packer = CANPacker(DBC[stock.carFingerprint][Bus.pt])
    ticks = 0

    def recv(wait_for_one=False):
      nonlocal ticks
      ticks += 1
      return [] if not live_ci else [TimestampedCanPacket(physical_frames(live_ci[0].CP, packer, ticks), time.clock_gettime_ns(time.CLOCK_BOOTTIME))]

    def prepare(cp, candidate_id, fingerprints, firmware):
      startup, sent = owner(cp, session=mode != "untouched", reply=b"\x7f\x28\x22" if mode in ("restored", "uncertain") else None, restore=mode != "uncertain")
      startup.callbacks = (recv, sent.extend)
      holder.owner = startup
      return startup.prepare(admission=lambda: True)

    fp = gen_empty_fingerprint()
    fp[2].update({0x110: 32, 0x362: 32} if stock.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else {0x50: 16, 0x2A4: 24})
    fp[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16, 0x36A: 16})
    identity = (stock.carFingerprint, fp, "0" * 17, [], stock.fingerprintSource, True)
    subscriber = messaging.sub_sock("carParams", timeout=100, conflate=True)
    ci = card = controls = None
    try:
      with patch.object(car_helpers, "fingerprint", return_value=identity), patch("opendbc.car.hyundai.torque_ev_startup.time.sleep"):
        if mode == "uncertain":
          with pytest.raises(RuntimeError, match="restoration unverified"):
            car_helpers.get_car(recv, lambda frames: None, lambda *args: None, True, False, pre_create_hook=prepare)
          assert saved.get("CarParams") is None
          assert messaging.recv_one(subscriber) is None
          assert not holder.owner.published
          return
        ci = car_helpers.get_car(recv, lambda frames: None, lambda *args: None, True, False, pre_create_hook=prepare)
        live_ci.append(ci)
        expected = ci.CP.to_dict()
        if aol:
          from openpilot.starpilot.car.hyundai.aol import policy_for

          policy = policy_for(ci.CP)
          assert policy.intent_supported
          expected["safetyConfigs"][0]["safetyParam"] |= policy.safety_param_addition
          expected["alternativeExperience"] |= policy.alternative_experience_addition
        card = Car(CI=ci, RI=RadarInterface(ci.CP), startup_owner=holder.owner)
      assert holder.owner.published and holder.owner.ready and holder.owner.prepared_for(card.CP)
      assert card.CP.to_dict() == expected
      assert card.CP.openpilotLongitudinalControl == (mode == "sent")
      if mode == "sent":
        for tick in range(1, 9):
          frames = physical_frames(ci.CP, packer, tick)
          assert all(frame.address != 0x1A0 for frame in frames)
          stamp = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
          state = ci.update([(stamp, frames)])
        assert state.canValid and not state.canTimeout
        assert holder.owner.sources_current(ci, stamp)
        assert not holder.owner.before_control(configured=False, sources_current=True, control_current=True)
        assert holder.owner.before_control(configured=True, sources_current=holder.owner.sources_current(ci, stamp), control_current=True)
        assert not holder.owner.handed_off
        card.publish_sendcan([make_tester_present_msg(0x730, 1, suppress_response=True)], valid=True)
        assert holder.owner.handed_off
      with structs.CarParams.from_bytes(saved.get("CarParams")) as reader:
        assert reader.to_dict() == expected
      event = None
      for _ in range(10):
        card.car_params_published = False
        card.state_publish(ci.update([]), None)
        event = messaging.recv_one(subscriber)
        if event is not None:
          break
        time.sleep(0.01)
      assert event is not None and event.valid and event.carParams.to_dict() == expected
      controls = Controls()
      assert controls.CP.to_dict() == expected
      feed(controls, 1_000_000_000, 0, active=False, enabled=False, can_valid=False, can_timeout=True)
      command, lateral_log = controls.state_control()
      assert not command.enabled and not command.latActive and not command.longActive
      controls.publish(command, lateral_log)
    finally:
      if holder.owner is not None and not holder.owner.published and holder.owner.outcome is not Outcome.ABORT_UNCERTAIN:
        holder.owner.close()
      del controls, card, ci, subscriber
      live_ci.clear()
      gc.collect()


def test_exact_manual_source_does_not_invent_a_different_topology(stock):
  from opendbc.car import structs

  manual = stock.as_reader().as_builder()
  manual.fingerprintSource = structs.CarParams.FingerprintSource.fixed
  promoted = candidate(manual, requested=True, is_release=False)
  assert promoted is not None
  assert promoted.fingerprintSource == structs.CarParams.FingerprintSource.fixed
  assert required(promoted)
  assert candidate(manual, requested=False, is_release=False) is None
