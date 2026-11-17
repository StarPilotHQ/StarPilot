from types import SimpleNamespace

import pytest

from opendbc.car import structs
from opendbc.car.gm import gmcan
from opendbc.car.gm.bolt_cc import button_bytes
from opendbc.car.gm.carcontroller import bolt_acc_pedal_dashboard_drive_ready
from opendbc.car.gm.tests import test_bolt_pedal as host_fixture
from opendbc.car.gm.values import CAR, GMSafetyFlags
from opendbc.safety.tests import test_gm_bolt_pedal as native_fixture
from opendbc.safety.tests.libsafety import libsafety_py


@pytest.mark.parametrize('gear,manual,paddle,expected', [
  (4, 0, 0, True), (6, 0, 0, True), (5, 1, 2, True),
  (5, 0, 2, False), (5, 1, 0, False), (5, 1, 1, False), (5, 1, 3, False),
  (0, 1, 2, False), (1, 1, 2, False), (2, 1, 2, False), (3, 1, 2, False),
  (4, 1, 2, False), (6, 1, 2, False), (7, 1, 2, False),
])
def test_native_dashboard_physical_paddle_scope(gear, manual, paddle, expected):
  case = native_fixture.TestGmBoltPedalSafety()
  case.setUp()
  case.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
  safety = case.safety
  safety.set_timer(1)
  for name, values in (
    ('ECMPRDNL2', {'PRNDL2': gear, 'ManualMode': manual}),
    ('AcceleratorPedal2', {'CruiseState': 0}), ('ECMEngineStatus', {'CruiseMainOn': 1}),
    ('PSCMStatus', {}), ('EBCMWheelSpdRear', {}),
    ('EBCMRegenPaddle', {'RegenPaddle': paddle}), ('ASCMSteeringButton', {}),
  ):
    assert safety.safety_rx_hook(case.stock(name, values))
  assert safety.safety_rx_hook(case.sensor(1))
  safety.safety_tick()
  assert safety.safety_config_valid()
  hud = structs.CarControl.HUDControl(leadDistanceBars=3)
  dashboard = gmcan.create_acc_dashboard_command(case.packer, 0, False, 72., hud, False, cruise_state=2)
  assert bool(safety.safety_tx_hook(case.packet(dashboard))) == expected
  assert safety.safety_fwd_hook(2, 0x370) == (-1 if expected else 0)
  active = gmcan.create_acc_dashboard_command(case.packer, 0, True, 72., hud, False, cruise_state=2)
  assert not safety.safety_tx_hook(case.packet(active))
  if manual or gear not in (4, 6):
    safety.set_controls_allowed(True)
    assert not safety.safety_tx_hook(case.packet(gmcan.create_pedal_command(case.packer, .2, 1)))
    assert case.recorded() == []


@pytest.mark.parametrize('invalid', ['paddle_stale', 'gear_stale', 'sensor_fault', 'main_off', 'relay', 'reset'])
def test_native_dashboard_paddle_keeps_source_and_fault_guards(invalid):
  case = native_fixture.TestGmBoltPedalSafety()
  case.setUp()
  case.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
  s = case.safety
  s.set_timer(1)
  assert s.safety_rx_hook(case.stock('ECMPRDNL2', {'PRNDL2': 5, 'ManualMode': 1}))
  assert s.safety_rx_hook(case.stock('EBCMRegenPaddle', {'RegenPaddle': 2}))
  s.set_timer(100002)
  for name, values in (
    ('ECMPRDNL2', {'PRNDL2': 5, 'ManualMode': 1}),
    ('EBCMRegenPaddle', {'RegenPaddle': 2}),
    ('AcceleratorPedal2', {'CruiseState': 0}),
    ('ECMEngineStatus', {'CruiseMainOn': int(invalid != 'main_off')}),
    ('PSCMStatus', {}), ('EBCMWheelSpdRear', {}), ('ASCMSteeringButton', {}),
  ):
    if (invalid == 'paddle_stale' and name == 'EBCMRegenPaddle' or
        invalid == 'gear_stale' and name == 'ECMPRDNL2'):
      continue
    assert s.safety_rx_hook(case.stock(name, values))
  assert s.safety_rx_hook(case.sensor(2, state=int(invalid == 'sensor_fault')))
  s.safety_tick()
  if invalid == 'relay':
    s.set_relay_malfunction(True)
  if invalid == 'reset':
    case.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
  dashboard = gmcan.create_acc_dashboard_command(case.packer, 0, False, 72., structs.CarControl.HUDControl(), False, cruise_state=2)
  assert not s.safety_tx_hook(case.packet(dashboard))
  assert s.safety_fwd_hook(2, 0x370) == (-1 if invalid == 'relay' else 0)


@pytest.mark.parametrize('bad', ['none', 'stale_paddle', 'future_paddle', 'stale_gear', 'stale_main', 'not_physical', 'not_regen'])
def test_host_dashboard_paddle_does_not_expand_default_drive_gate(bad):
  now = 1_000_000_000
  cs = SimpleNamespace(
    out=SimpleNamespace(gearShifter=structs.CarState.GearShifter.manumatic, regenBraking=bad != 'not_regen'),
    bolt_acc_dashboard_paddle=(now, bad != 'not_physical'),
    bolt_pedal_gear_ts_nanos=now, bolt_pedal_main_ts_nanos=now,
  )
  if bad in ('stale_paddle', 'future_paddle'):
    cs.bolt_acc_dashboard_paddle = (now - 100_000_001 if bad == 'stale_paddle' else now + 1, True)
  if bad == 'stale_gear':
    cs.bolt_pedal_gear_ts_nanos -= 100_000_001
  if bad == 'stale_main':
    cs.bolt_pedal_main_ts_nanos -= 300_000_001
  assert not bolt_acc_pedal_dashboard_drive_ready(cs, now)
  assert bolt_acc_pedal_dashboard_drive_ready(cs, now, allow_paddle=True) == (bad == 'none')


def test_actual_parser_controller_six_physical_paddle_cycles_keep_inactive_dashboard():
  fixture = host_fixture.TestBoltPedalStartupParser()
  cp = host_fixture.params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, pedal=True, camera=True)
  native = libsafety_py.libsafety
  assert native.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  native.init_tests()
  native.reset_recorded_can()
  ci, _ = fixture.stream(cp, native=native)
  packer = ci.CS.pedal_packer
  control = structs.CarControl()
  for tick in range(144):
    pressed = tick % 24 in range(4, 20)
    now = 3_500_000_000 + tick * 10_000_000
    values = {
      'ECMPRDNL2': {'PRNDL2': 5 if pressed else 4, 'ManualMode': int(pressed)},
      'EBCMRegenPaddle': {'RegenPaddle': 2 if pressed else 0},
      'ECMEngineStatus': {'CruiseMainOn': 1, 'BrakePressed': 1},
      'AcceleratorPedal2': {'CruiseState': 0}, 'BCMDoorBeltStatus': {'LeftSeatBelt': 1},
    }
    frames = [packer.make_can_msg(name, 0, values.get(name, {})) for name in fixture.PT_MESSAGES
              if name not in ('GAS_SENSOR', 'ASCMSteeringButton')]
    frames += [host_fixture.TestBoltPedalMessages.sensor(packer, 0, (13 + tick) % 16),
               (0x1E1, button_bytes(1, tick % 4), 0),
               packer.make_can_msg('ASCMLKASteeringCmd', 2, {}),
               packer.make_can_msg('AEBCmd', 2, {}),
               packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {}),
               gmcan.create_friction_brake_command(ci.CC.packer_ch, 2, 0, (tick // 4) % 4, False, False, False, cp)]
    native.set_timer(now // 1000)
    for address, raw, bus in frames:
      assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, raw))
    native.safety_tick()
    state = ci.update([(now, frames)])
    assert state.canValid and not state.canTimeout
    assert state.regenBraking == pressed
    assert state.brakePressed
    ci.CC.frame = 104 + tick
    _, messages = ci.apply(control.as_reader(), now)
    dashboard = [m for m in messages if m[0] == 0x370]
    assert bool(dashboard) == (tick % 4 == 0)
    for address, raw, bus in dashboard:
      assert not (raw[2] & 0x80)
      assert native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, raw))
      assert native.safety_fwd_hook(2, 0x370) == -1
    assert not any(m[0] in (0xBD, 0x1F5) for m in messages)
    for address, raw, _ in messages:
      if address == 0x315:
        assert raw[:2] == bytes((0x10, 0))
      if address == 0x200:
        assert raw[:4] == bytes(4) and not (raw[4] & 0x80)
    if pressed:
      assert not ci.CC.bolt_pedal_admission(control, ci.CS, now)[0]
