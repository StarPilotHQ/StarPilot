"""Actual Card caller and mixed owner ordering; no vehicle/protocol recovery claim."""
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.blended_longitudinal import BlendedStartup, candidate_from_stock, Outcome
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_palisade_2023 import params, feed
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.vehicle_startup import VehicleStartupOwner


class Clock:
  ns = 1_120_000_000

  def seconds(self):
    return self.ns / 1e9


class SM:
  def __init__(self, clock):
    self.clock = clock
    self.command = structs.CarControl().as_reader()
    self.valid = {'carControl': True}
    self.alive = {'carControl': True}
    self.seen = {'carControl': True}
    self.refresh()

  def refresh(self):
    self.logMonoTime = {'carControl': self.clock.ns}
    self.recv_time = {'carControl': self.clock.seconds()}

  def __getitem__(self, key):
    assert key == 'carControl'
    return self.command

  def all_alive(self, services):
    return all(self.alive[name] for name in services)


@pytest.fixture
def pending():
  clock = Clock()
  stock = params('hdaii')
  cp = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
  sent = []
  startup = BlendedStartup(stock, cp, (Mock(), lambda frames: sent.extend(frames)))
  startup.clock = clock.seconds
  startup.owner.exchange = Mock(return_value=True)
  startup.owner.restore_exchange = Mock(return_value=True)
  admission = Mock(return_value=True)
  with patch('threading.Thread'):
    assert startup.prepare(admission=admission) is cp
    ci = CarInterface(cp)
    startup.configure(ci)
    startup.finalize_aol_configuration(ci)
    startup.seal_publication()
    for tick in range(12):
      cs = feed(ci, tick)
    card = Car.__new__(Car)
    card.CP, card.CI = cp, ci
    card.vehicle_startup = VehicleStartupOwner(startup)
    card.sm = cast(messaging.SubMaster, SM(clock))
    card.ci_initialized = False
    card.aol_replay = False
    card.ioniq6_long_pending = False
    card.ioniq6_long_selected = False
    card.ioniq6_long_prearmed = False
    card.params = cast(Params, SimpleNamespace(put_bool=Mock()))
    card.startup_panda_configured = Mock(return_value=False)
    card.publish_sendcan = Mock()
    card.learning_send_committed_mono_ns = 0
    ci.apply = Mock(return_value=(None, []))
    with patch('openpilot.selfdrive.car.card.time.monotonic_ns', side_effect=lambda: clock.ns), \
         patch('openpilot.selfdrive.car.card.time.monotonic', side_effect=clock.seconds), \
         patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', side_effect=lambda _: clock.ns):
      yield SimpleNamespace(card=card, startup=startup, clock=clock, cp=cp, stock=stock,
                            cs=cs, ci=ci, sent=sent, admission=admission)
    startup.close()


def test_pending_constructor_and_six_second_host_initialization_keep_factory_output(pending):
  f = pending
  frozen = f.cp.to_dict()
  assert f.startup.pending_target and not f.startup.owner.active()
  assert f.startup.prepared_for(f.cp)
  assert f.startup.owner.published
  for tick in range(64):
    f.clock.ns = 1_120_000_000 + tick * 100_000_000
    f.card.sm.refresh()
    # This is the real Card initialize_only entry used while the host is warming.
    assert not f.card.gm_startup_keepalive()
    f.card.controls_update(f.cs, f.card.sm.command, initialize_only=True)
    assert not f.card.ci_initialized
  f.startup.owner.exchange.assert_not_called()
  f.ci.apply.assert_not_called()
  f.card.params.put_bool.assert_not_called()
  assert not f.sent
  assert f.cp.to_dict() == frozen
  assert f.stock.pcmCruise and not f.stock.openpilotLongitudinalControl


def test_actual_card_waits_for_current_inactive_sources_then_ack_native_and_post_ack_can(pending):
  f = pending
  f.card.sm.valid['carControl'] = False
  f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.exchange.assert_not_called()
  f.card.sm.valid['carControl'] = True
  f.card.sm.logMonoTime['carControl'] = f.clock.ns - 150_000_001
  f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.exchange.assert_not_called()
  f.card.sm.refresh()
  f.card.sm.recv_time['carControl'] = (f.clock.ns - 150_000_001) / 1e9
  f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.exchange.assert_not_called()
  f.card.sm.refresh()
  active = structs.CarControl()
  active.longActive = True
  f.card.controls_update(f.cs, active.as_reader())
  f.startup.owner.exchange.assert_not_called()
  f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.exchange.assert_called_once()
  assert f.startup.owner.exchange.call_args.kwargs == {'bus': 1, 'addr': 0x730, 'com_cont_req': b'\x28\x03\x01', 'reset': True}
  assert f.startup.owner.result.outcome is Outcome.OWNED
  assert not f.startup.pending_target
  f.card.params.put_bool.assert_called_once_with('ControlsReady', True)
  f.ci.apply.assert_not_called()  # ELM327/mismatched Panda cannot transmit.
  f.card.startup_panda_configured.return_value = True
  f.card.controls_update(f.cs, f.card.sm.command, initialize_only=True)
  f.ci.apply.assert_not_called()  # Only pre-suppression CAN exists.
  f.clock.ns = 1_140_000_000
  f.card.sm.refresh()
  f.cs = feed(f.ci, 13)
  f.card.controls_update(f.cs, f.card.sm.command, initialize_only=True)
  f.ci.apply.assert_called_once()
  command = f.ci.apply.call_args.args[0]
  assert not (command.enabled or command.latActive or command.longActive)
  assert command.actuators.torque == command.actuators.accel == 0.0


def test_failed_handoff_restores_then_aborts_without_relabeling_published_target(pending):
  f = pending
  frozen = f.cp.to_dict()
  f.startup.owner.exchange.return_value = False
  with pytest.raises(RuntimeError, match='abort published target'):
    f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.restore_exchange.assert_called_once()
  assert f.cp.to_dict() == frozen
  assert f.cp.openpilotLongitudinalControl and not f.cp.pcmCruise
  f.card.params.put_bool.assert_not_called()
  f.ci.apply.assert_not_called()


def test_lost_admission_never_disables_and_post_ack_blackout_is_bounded(pending):
  f = pending
  f.admission.return_value = False
  with pytest.raises(RuntimeError, match='lost diagnostic admission'):
    f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.exchange.assert_not_called()
  f.admission.return_value = True
  f.card.controls_update(f.cs, f.card.sm.command)
  f.clock.ns += 1_010_000_000
  f.startup._tester()
  with pytest.raises(RuntimeError, match='Startup keepalive bound exceeded'):
    f.startup.check()
  f.ci.apply.assert_not_called()


def test_pending_sources_do_not_grant_button_enable_and_wrong_bus_blocks_takeover(pending):
  f = pending
  f.cs.buttonEnable = True
  f.startup.after_state(ci=f.ci, state=f.cs, now_ns=f.clock.ns, control_enabled=False,
                        control_current=True, pandas=[], panda_log_ns=0, panda_recv_ns=0, panda_current=False)
  assert not f.cs.buttonEnable and f.cs.blockPcmEnable
  from opendbc.car import Bus
  f.ci.can_parsers[Bus.pt].bus = 0
  f.card.controls_update(f.cs, f.card.sm.command)
  f.startup.owner.exchange.assert_not_called()
  f.ci.apply.assert_not_called()


def test_late_handoff_actual_controller_and_native_accept_neutral_replacement(pending, request):
  # ACK and native-health publication are explicit fixture boundaries. The
  # controller, send serialization and native TX hooks below execute unchanged.
  from types import MethodType
  from opendbc.safety.tests import common
  from opendbc.safety.tests.libsafety import libsafety_py
  from openpilot.cereal import messaging
  from openpilot.selfdrive.pandad import can_capnp_to_list

  f = pending
  native = libsafety_py.libsafety

  def reset_native():
    native.set_alternative_experience(0)
    assert native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0) == 0
    native.init_tests()

  request.addfinalizer(reset_native)
  native.set_alternative_experience(f.cp.alternativeExperience)
  f.card.controls_update(f.cs, f.card.sm.command)
  assert f.startup.owner.active() and not f.startup.handed_off
  f.ci.apply.assert_not_called()
  config = f.cp.safetyConfigs[0]
  assert native.set_safety_hooks(config.safetyModel.raw, config.safetyParam) == 0
  native.init_tests()
  assert not native.get_controls_allowed()
  f.card.startup_panda_configured.return_value = True
  f.clock.ns = 1_140_000_000
  f.card.sm.refresh()
  f.cs = feed(f.ci, 13)
  camera_address, camera_data, camera_bus = f.ci.CC.packer.make_can_msg('CAM_0x2a4', 2, f.ci.CS.lfa_block_msg)
  assert native.safety_rx_hook(common.make_msg(camera_bus, camera_address, dat=camera_data))
  f.ci.apply = MethodType(CarInterface.apply, f.ci)
  published = []

  def send(service, packet):
    assert service == 'sendcan'
    decoded = messaging.log_from_bytes(packet)
    assert decoded.valid
    frames = can_capnp_to_list([packet], msgtype='sendcan')[0][1]
    assert frames
    for address, data, bus in frames:
      assert native.safety_tx_hook(common.make_msg(bus, address, dat=data)), hex(address)
    published.extend(frames)

  f.card.pm = SimpleNamespace(send=send)
  f.card.publish_sendcan = MethodType(Car.publish_sendcan, f.card)
  f.card.controls_update(f.cs, f.card.sm.command)
  assert f.startup.handed_off and f.startup.controller.normal_sent
  assert {(address, bus) for address, _, bus in published} >= {(0x730, 1), (0x50, 0), (0x340, 1), (0x420, 1), (0x421, 1)}
  assert not native.get_controls_allowed() and not native.get_relay_malfunction()
  assert f.ci.CS.out.canValid and not f.ci.CS.out.accFaulted
