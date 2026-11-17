"""Explicit plain OBD alpha owner, separate from conventional CC and camera owners."""

from types import SimpleNamespace
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.gm.aol import qualified_gm
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_cc import feed
from opendbc.car.gm.tests.test_volt_camera_automatic import recorded_fingerprint
from opendbc.car.gm.values import CAR, DBC, is_volt_flashed_profile, is_volt_gateway_longitudinal
from opendbc.safety.tests.libsafety import libsafety_py


def params(alpha=True, release=False, fp=None):
  return CarInterface.get_params(CAR.CHEVROLET_VOLT, recorded_fingerprint() if fp is None else fp, [], alpha, release, False)


def tick(ci, packer, now, index=0, analog=0, brake=False, regen=False, gas=False, friction=False, button=1):
  _, frames = feed(
    SimpleNamespace(update=lambda _: None), packer, now, counter=index % 4, camera=False, active=False, brake=brake, regen=regen, gas=gas, speed=15
  )
  frames = [m for m in frames if m[0] not in (0xBE, 0x1C4, 0x1E1, 0x232)]
  frames += [
    packer.make_can_msg('EBCMBrakePedalPosition', 0, {'BrakePedalPosition': analog}),
    packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 3, 'AcceleratorPedal2': 30 if gas else 0}),
    packer.make_can_msg('EBCMFrictionBrakeStatus', 0, {'FrictionBrakeUnavailable': int(friction)}),
    packer.make_can_msg('ASCMSteeringButton', 0, {'ACCButtons': button, 'ACCAlwaysOne': 1, 'RollingCounter': index % 4}),
  ]
  return ci.update([(now, frames)]), frames


@pytest.mark.parametrize('alpha', [False, True])
def test_plain_volt_restores_flashed_obd_owner(alpha):
  cp = params(alpha)
  cp.alternativeExperience = 32
  assert is_volt_flashed_profile(cp) and not cp.dashcamOnly and qualified_gm(cp)
  assert cp.radarUnavailable and cp.alphaLongitudinalAvailable and not cp.pcmCruise
  assert cp.openpilotLongitudinalControl == alpha and is_volt_gateway_longitudinal(cp) == alpha
  assert cp.safetyConfigs[0].safetyParam == 0xC004
  assert cp.minSteerSpeed == pytest.approx(7 * CV.MPH_TO_MS) and cp.minEnableSpeed == -1


@pytest.mark.parametrize('mutation', ['pedal', 'camera', 'sascm', 'radar', 'brake', 'wheel_dlc', 'release'])
def test_other_installations_do_not_enter_flashed_owner(mutation):
  fp = recorded_fingerprint()
  bus, address, length = {
    'pedal': (0, 0x201, 6),
    'camera': (2, 0x320, 6),
    'sascm': (0, 0x2FF, 8),
    'radar': (1, 0x460, 8),
    'brake': (0, 0xBE, 6),
    'wheel_dlc': (0, 0x34A, 8),
    'release': (0, 0xF1, 6),
  }[mutation]
  fp[bus][address] = length
  assert not is_volt_flashed_profile(params(fp=fp, release=mutation == 'release'))


@pytest.mark.parametrize(
  'analog,foot,regen,gas', [(0, False, False, False), (8, False, False, False), (0, True, False, False), (0, False, True, False), (0, False, False, True)]
)
def test_parser_retains_driver_inputs(analog, foot, regen, gas):
  cp = params()
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for i in range(40):
    cs, _ = tick(ci, packer, 1_000_000_000 + i * 10_000_000, i, analog, foot, regen, gas)
  assert cs.canValid and not cs.canTimeout and not cs.accFaulted and not cs.cruiseState.enabled
  assert cs.brakePressed == (analog >= 6 or foot) and cs.regenBraking == regen and cs.gasPressed == gas


def test_friction_unavailability_stays_a_fault():
  cp = params()
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for i in range(40):
    cs, _ = tick(ci, packer, 1_000_000_000 + i * 10_000_000, i, friction=True)
  assert cs.canValid and cs.accFaulted


@pytest.mark.parametrize("accel", [-1.5, 1.0])
def test_native_direct_long_uses_set_without_stock_cruise(accel):
  cp = params()
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 0xC004) == 0
  for i in range(80):
    now = 1_000_000_000 + i * 10_000_000
    safety.set_timer(now // 1000)
    safety.set_aol_test_heartbeat(True)
    safety.aol_set_host_request(3)
    cs, frames = tick(ci, packer, now, i, button=3 if 40 <= i < 44 else 1)
    for a, d, b in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(a, b, d))
    safety.safety_tick()
    if i < 40:
      assert not safety.get_controls_allowed() and not safety.aol_get_permission_mask() & 2
  assert cs.canValid and not cs.accFaulted and not cs.cruiseState.enabled
  assert safety.get_controls_allowed() and safety.aol_get_permission_mask() == 3
  cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
  cc.actuators.accel = accel
  cc.actuators.longControlState = "pid"
  sent = []
  for i in range(80, 90):
    now = 1_000_000_000 + i * 10_000_000
    safety.set_timer(now // 1000)
    cs, frames = tick(ci, packer, now, i)
    for a, d, b in frames:
      safety.safety_rx_hook(libsafety_py.make_CANPacket(a, b, d))
    _, messages = ci.apply(cc.as_reader(), now)
    sent.extend(messages)
    for a, d, b in messages:
      assert safety.safety_tx_hook(libsafety_py.make_CANPacket(a, b, d)), hex(a)
  assert any(a == 0x2CB and b == 0 for a, _, b in sent) and any(a == 0x315 and b == 0 for a, _, b in sent)
  assert any(a == 0x2CB and d[0] & 1 for a, d, _ in sent)
  if accel < 0:
    assert any(a == 0x315 and d[0] >> 4 in (0xA, 0xD) for a, d, _ in sent)
  assert not any(a == 0x1E1 for a, _, _ in sent)


def test_actual_set_drives_selfdrived_and_controls_without_stock_cruise():
  from contextlib import ExitStack
  from openpilot.cereal import messaging, log
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

  cp = params()
  cp.alternativeExperience = 32
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, controls, step = case._authority_callers(False, cp=cp, genuine_events=True)
    ci = CarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    safety = libsafety_py.libsafety
    safety.init_tests()
    safety.set_alternative_experience(32)
    assert safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 0xC004) == 0
    for i in range(320):
      now = 10_000_000_000 + i * 10_000_000
      safety.set_timer(now // 1000)
      safety.set_aol_test_heartbeat(True)
      safety.aol_set_host_request(3 if sd.enabled else 1)
      cs, frames = tick(ci, packer, now, i, button=3 if 40 <= i < 44 else 1)
      for address, raw, bus in frames:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, raw))
      safety.safety_tick()
      mask, request = safety.aol_get_permission_mask(), safety.aol_get_request_mask()
      event = messaging.new_message('carState', valid=True, logMonoTime=now)
      event.carState = cs
      _, _, cc = step(
        i,
        state_stamp=now,
        car_state_message=event,
        allowed=bool(mask & 1),
        long_allowed=bool(mask & 2),
        native_request_lat=bool(request & 1),
        native_request_long=bool(request & 2),
        panda_controls_allowed=safety.get_controls_allowed(),
        panda_fault=not safety.safety_config_valid(),
      )
      assert not cs.cruiseState.enabled and not cs.accFaulted
      if i < 40:
        assert not cc.enabled and not cc.longActive
      if i > 80:
        assert cc.enabled and cc.latActive and cc.longActive
        assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
