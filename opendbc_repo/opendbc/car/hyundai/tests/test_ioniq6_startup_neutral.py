"""Prepared Ioniq 6 keeps inactive replacement traffic alive before model readiness."""
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests import test_ioniq6_handoff as handoff_tests
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py


def prepared_card(monkeypatch, *, alternate=False, offset=0, aol=False):
  clock = [10_000_000_000]
  monkeypatch.setattr(time, 'monotonic_ns', lambda: clock[0])
  monkeypatch.setattr(time, 'clock_gettime_ns', lambda _: clock[0])
  owner = handoff_tests.TestIoniq6PrepublicationHandoff().ready_card(ioniq_selected=True, bus_offset=offset)
  owner.CP = handoff_tests.ioniq6_profiles(alternate=alternate, bus_offset=offset)[2]
  if aol:
    owner.CP.safetyConfigs[-1].safetyParam |= 0x0800
  owner.CI = CarInterface(owner.CP)
  owner.CI.update([])
  owner.CI.CS.out = structs.CarState(canValid=True, standstill=True, gearShifter='park').as_reader()
  owner.ioniq6_long_pending = False
  owner.ioniq6_long_prearmed = owner.ioniq6_host_warmed = True
  owner.ioniq6_keepalive = SimpleNamespace(stop=Mock())
  owner.aol_replay = False
  owner._invalidate_feedback_output = Mock()
  control = structs.CarControl.new_message().as_reader()
  panda = SimpleNamespace(safetyModel=owner.CP.safetyConfigs[-1].safetyModel,
                          safetyParam=owner.CP.safetyConfigs[-1].safetyParam,
                          alternativeExperience=owner.CP.alternativeExperience,
                          safetyRxChecksInvalid=False, faults=[])
  pandas = ([SimpleNamespace(safetyModel=cfg.safetyModel, safetyParam=cfg.safetyParam,
                            alternativeExperience=owner.CP.alternativeExperience,
                            safetyRxChecksInvalid=False, faults=[])
             for cfg in list(owner.CP.safetyConfigs)[:-1]] + [panda])

  class SM:
    seen = dict.fromkeys(('carControl', 'pandaStates'), True)
    valid = dict.fromkeys(('carControl', 'pandaStates'), True)
    alive = dict.fromkeys(('carControl', 'pandaStates'), True)
    logMonoTime = dict.fromkeys(('carControl', 'pandaStates'), clock[0])
    recv_time = dict.fromkeys(('carControl', 'pandaStates'), clock[0] / 1e9)

    def __getitem__(self, key):
      return pandas if key == 'pandaStates' else control

    def all_alive(self, names):
      return all(self.alive[name] for name in names)

  owner.sm = SM()
  return owner, clock, control, panda


@pytest.fixture(scope='module', params=(False, True))
def native_safety(request, tmp_path_factory):
  release = request.param
  path = libsafety_py._build_libsafety(release=release, output_dir=tmp_path_factory.mktemp('ioniq6-startup-native'))
  return libsafety_py.ffi.dlopen(path), release


@pytest.mark.parametrize('aol', (False, True))
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_prepared_startup_sends_continuous_neutral_cruise_before_model(monkeypatch, alternate, offset, aol, native_safety):
  owner, clock, control, _ = prepared_card(monkeypatch, alternate=alternate, offset=offset, aol=aol)
  owner.ci_initialized = False
  owner.ioniq6_panda_diagnostic_ready = Mock(return_value=True)
  decoder = CANParser(DBC[CAR.HYUNDAI_IONIQ_6][Bus.pt], [('SCC_CONTROL', 0), ('LFA', 0)], owner.CI.CC.CAN.ECAN)
  native, release = native_safety
  assert native.set_safety_hooks(19, 0) == 0
  configured = native.set_safety_hooks(owner.CP.safetyConfigs[-1].safetyModel.raw, int(owner.CP.safetyConfigs[-1].safetyParam)) == 0
  assert configured
  native.set_controls_allowed(False)
  counts = []
  for tick in range(200):
    if not release:
      native.set_timer(clock[0] // 1000)
      names = (owner.CI.CS.accelerator_msg_canfd, 'TCS', 'WHEEL_SPEEDS', 'MDPS', owner.CI.CS.cruise_btns_msg_canfd)
      for name in names:
        packet = owner.CI.CC.packer.make_can_msg(name, 1, {'COUNTER': tick % (16 if name == 'CRUISE_BUTTONS' else 256)})
        assert native.safety_rx_hook(libsafety_py.make_CANPacket(packet[0], packet[2], packet[1]))
    owner.sm.logMonoTime = dict.fromkeys(('carControl', 'pandaStates'), clock[0])
    owner.sm.recv_time = dict.fromkeys(('carControl', 'pandaStates'), clock[0] / 1e9)
    owner.publish_sendcan.reset_mock()
    owner.controls_update(owner.CI.CS.out, control, initialize_only=True)
    assert owner.publish_sendcan.called, 'PREPARED_I6_STARTUP_MUST_REPLACE_CRUISE_BEFORE_MODEL'
    frames = owner.publish_sendcan.call_args.args[0]
    assert owner.publish_sendcan.call_args.kwargs['valid']
    decoder.update([(clock[0], frames)])
    for frame in frames:
      accepted = native.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2] % 4, frame[1]))
      if not release:
        assert accepted, (hex(frame[0]), frame[2], frame[1].hex(), owner.CI.CC.frame)
      elif frame[0] in (0x730, 0x1A0, 0x100):
        assert not accepted, 'Release must continue rejecting developer-only long traffic'
      if frame[0] == 0x1A0:
        counts.append(clock[0])
        assert decoder.vl['SCC_CONTROL']['ACCMode'] == 0
        assert decoder.vl['SCC_CONTROL']['aReqRaw'] == 0
        assert decoder.vl['SCC_CONTROL']['aReqValue'] == 0
      if frame[0] == 0x12A:
        assert decoder.vl['LFA']['ActToiSta'] == 0
        assert decoder.vl['LFA']['StrTqReqVal'] == 0
    clock[0] += 10_000_000
  assert len(counts) == 100
  assert all(b - a == 20_000_000 for a, b in zip(counts[:-1], counts[1:], strict=True))
  owner.params.put_bool.assert_called_once_with('ControlsReady', True)
  assert owner.ioniq6_panda_armed
  assert not owner.ioniq6_long_lost
  owner._invalidate_feedback_output.assert_called_with(assignment=True)


@pytest.mark.parametrize('fault', ('unprepared', 'unwarmed', 'lost', 'stale_control', 'active_control',
                                   'invalid_can', 'stale_panda', 'rx_invalid', 'hardware_fault', 'wrong_profile'))
def test_neutral_startup_retains_admission_checks(monkeypatch, fault):
  owner, _, control, panda = prepared_card(monkeypatch)
  owner.ci_initialized = True
  if fault == 'unprepared':
    owner.ioniq6_long_prearmed = False
  elif fault == 'unwarmed':
    owner.ioniq6_host_warmed = False
  elif fault == 'lost':
    owner.ioniq6_long_lost = True
  elif fault == 'stale_control':
    owner.sm.logMonoTime['carControl'] -= 150_000_001
  elif fault == 'active_control':
    control = control.as_builder()
    control.latActive = True
  elif fault == 'invalid_can':
    owner.CI.CS.out = structs.CarState(canValid=False).as_reader()
  elif fault == 'stale_panda':
    owner.sm.logMonoTime['pandaStates'] -= 300_000_001
  elif fault == 'rx_invalid':
    panda.safetyRxChecksInvalid = True
  elif fault == 'hardware_fault':
    panda.faults = [2]
  elif fault == 'wrong_profile':
    panda.safetyParam ^= 0x80
  owner.controls_update(owner.CI.CS.out, control, initialize_only=True)
  owner.publish_sendcan.assert_not_called()
  assert owner.CI.CC.frame == 0


@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_verified_handoff_starts_neutral_before_first_control_message(monkeypatch, alternate, offset):
  owner, _, control, panda = prepared_card(monkeypatch, alternate=alternate, offset=offset, aol=True)
  owner.sm.seen['carControl'] = owner.sm.valid['carControl'] = owner.sm.alive['carControl'] = False
  owner.sm.logMonoTime['carControl'] = owner.sm.recv_time['carControl'] = 0
  owner.ci_initialized = False
  owner.ioniq6_panda_diagnostic_ready = Mock(return_value=True)
  expected_model, expected_param = panda.safetyModel, panda.safetyParam
  panda.safetyModel, panda.safetyParam = 3, 1
  owner.controls_update(owner.CI.CS.out, control, initialize_only=True)
  owner.params.put_bool.assert_called_once_with('ControlsReady', True)
  owner.publish_sendcan.assert_not_called()
  panda.safetyModel, panda.safetyParam = expected_model, expected_param
  owner.controls_update(owner.CI.CS.out, control, initialize_only=True)
  assert owner.publish_sendcan.called, 'VERIFIED_HANDOFF_CANNOT_WAIT_FOR_FIRST_MODEL_CONTROL'
  frames = owner.publish_sendcan.call_args.args[0]
  decoder = CANParser(DBC[CAR.HYUNDAI_IONIQ_6][Bus.pt], [('SCC_CONTROL', 0), ('LFA', 0)], owner.CI.CC.CAN.ECAN)
  decoder.update([(10_000_000_000, frames)])
  assert any(frame[0] == 0x1A0 for frame in frames)
  assert decoder.vl['SCC_CONTROL']['ACCMode'] == 0
  assert decoder.vl['SCC_CONTROL']['aReqRaw'] == decoder.vl['SCC_CONTROL']['aReqValue'] == 0
  assert decoder.vl['LFA']['ActToiSta'] == decoder.vl['LFA']['StrTqReqVal'] == 0


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('fault', (None, 'not_prepared', 'already_configured', 'stale_panda', 'hardware_fault', 'wrong_model'))
def test_native_startup_readiness_is_immediate_before_selfdrive_events(monkeypatch, offset, fault):
  from openpilot.cereal import log

  owner, _, control, panda = prepared_card(monkeypatch, alternate=True, offset=offset, aol=True)
  owner.ci_initialized = False
  owner.ioniq6_host_warmed = False
  owner.sm.seen['carControl'] = owner.sm.seen['onroadEvents'] = False
  owner.sm.valid['carControl'] = owner.sm.alive['carControl'] = False
  owner.sm.update = Mock()
  pandas = owner.sm['pandaStates']
  for index, state in enumerate(pandas):
    state.safetyModel, state.safetyParam = structs.CarParams.SafetyModel.elm327, 1
    state.controlsAllowed = False
    state.ignitionLine, state.ignitionCan = True, False
    state.pandaType = log.PandaState.PandaType.redPanda if index == len(pandas) - 1 else log.PandaState.PandaType.dos
  if fault == 'not_prepared':
    owner.ioniq6_long_prearmed = False
  elif fault == 'already_configured':
    owner.ci_initialized = True
  elif fault == 'stale_panda':
    owner.sm.logMonoTime['pandaStates'] -= 300_000_001
  elif fault == 'hardware_fault':
    panda.faults = [log.PandaState.FaultType.interruptRateCan1]
  elif fault == 'wrong_model':
    panda.safetyModel = structs.CarParams.SafetyModel.hyundaiCanfd

  class QueuedParams:
    def __init__(self):
      self.visible = False
      self.queued = []

    def get_bool(self, name):
      return self.visible if name == 'ControlsReady' else False

    def put_bool(self, name, value, *, block=False):
      assert name == 'ControlsReady'
      if block:
        self.visible = value
      else:
        self.queued.append(value)

  owner.params = QueuedParams()
  original_sm = owner.sm

  class BeforeEvents:
    def __getattr__(self, name):
      return getattr(original_sm, name)

    def __getitem__(self, key):
      return [] if key == 'onroadEvents' else original_sm[key]

  owner.sm = BeforeEvents()
  owner.state_update = Mock(return_value=(owner.CI.CS.out, None))
  owner.state_publish = Mock()
  owner.radar_publish = Mock()
  owner.radar_update = Mock(return_value=None)
  owner._deferred_radar_can = None
  owner.step()
  assert owner.params.visible is (fault is None), 'VERIFIED_STARTUP_READY_MUST_NOT_WAIT_FOR_BACKGROUND_WRITES_OR_MODEL_EVENTS'
  assert not owner.params.queued
  owner.publish_sendcan.assert_not_called()
  assert not owner.ioniq6_host_warmed
  assert owner.CI.CC.frame == 0
