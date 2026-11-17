"""Conventional-cruise truck layout uses the existing guarded CC button owner."""
import unittest

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.aol import qualified_gm
from opendbc.car.gm.values import CAR, DBC, GMFlags, SILVERADO_CC_STOCK_SOURCES, is_ordinary_cc_profile
from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
from opendbc.car.gm.tests.test_bolt_cc import native
from opendbc.car.gm.tests.test_cc_gateway_stock import silverado_stock_params
from opendbc.car.gm.tests.test_ordinary_cc import qualified_frames, warm_ordinary_native
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_gm as gm_fixture


def fingerprint():
  fp = gen_empty_fingerprint()
  fp[0].update(SILVERADO_CC_STOCK_SOURCES)
  fp[0].update({0x17D: 8, 0xBE: 6, 0x142: 7})
  fp[2].update({0x180: 4, 0x320: 6, 0x370: 6})
  return fp


def params(*, alpha=False, release=False, disabled=False):
  cp = CarInterface.get_params(CAR.CHEVROLET_SILVERADO_CC, fingerprint(), [], alpha, release, False)
  prepare_disable_longitudinal(cp, disabled)
  return cp


@pytest.mark.parametrize('release', [False, True])
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('disabled', [False, True])
def test_sierra_layout_finalizes_existing_cc_owner_and_preserves_stock_variant(release, alpha, disabled):
  cp = params(alpha=alpha, release=release, disabled=disabled)
  assert not cp.dashcamOnly and not cp.passive
  assert cp.openpilotLongitudinalControl == (not disabled) and not cp.pcmCruise
  assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.gm
  assert cp.safetyConfigs[0].safetyParam == 0xC160 and cp.networkLocation == structs.CarParams.NetworkLocation.gateway
  assert cp.flags == int(GMFlags.CC_LONG | GMFlags.HAS_BSM)
  assert is_ordinary_cc_profile(cp) and qualified_gm(cp)
  assert cp.minSteerSpeed == pytest.approx(7 * .44704)
  assert cp.minEnableSpeed == pytest.approx(24 * .44704)
  old = silverado_stock_params()
  assert old.safetyConfigs[0].safetyParam == 16 and old.pcmCruise and not old.openpilotLongitudinalControl
  assert not old.dashcamOnly and not is_ordinary_cc_profile(old)


@pytest.mark.parametrize('bus,address', [(0, a) for a in (*SILVERADO_CC_STOCK_SOURCES, 0xBE)] +
                                      [(2, 0x180), (2, 0x320), (2, 0x370)])
def test_incomplete_sierra_layout_cannot_gain_control(bus, address):
  fp = fingerprint()
  fp[bus].pop(address)
  cp = CarInterface.get_params(CAR.CHEVROLET_SILVERADO_CC, fp, [], False, False, False)
  assert cp.dashcamOnly
  assert not qualified_gm(cp) and not is_ordinary_cc_profile(cp)


@pytest.mark.parametrize('change', ['esp_length', 'camera_length', 'radar', 'pedal'])
def test_conflicting_layout_does_not_enter_sierra_cc_button_owner(change):
  fp = fingerprint()
  if change == 'esp_length':
    fp[0][0x17D] = 7
  elif change == 'camera_length':
    fp[2][0x320] = 8
  elif change == 'radar':
    fp[1][0x460] = 8
  else:
    fp[0][0x201] = 6
  cp = CarInterface.get_params(CAR.CHEVROLET_SILVERADO_CC, fp, [], False, False, False)
  assert not is_ordinary_cc_profile(cp)


def test_sierra_name_and_actual_card_startup_no_longer_become_dashcam():
  assert any(doc.name == 'GMC Sierra 1500 No-ACC 2020-21' for doc in CAR.CHEVROLET_SILVERADO_CC.config.car_docs)
  with OpenpilotPrefix():
    card = gm_fixture.TestGmAol.card(params(), Params())
    try:
      assert not card.CP.passive and not card.CP.dashcamOnly and not card.CP.notCar
      assert is_ordinary_cc_profile(card.CP) and qualified_gm(card.CP)
    finally:
      card.vehicle_startup.close()


@pytest.mark.parametrize('disabled', [False, True])
def test_sierra_parser_sender_and_native_permissions(disabled):
  cp = params(disabled=disabled)
  assert not cp.dashcamOnly
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(cp.alternativeExperience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  warm_ordinary_native(unittest.TestCase(), ci, packer, safety)
  control = structs.CarControl(enabled=True, latActive=True, longActive=not disabled)
  control.actuators.torque = .1
  control.actuators.accel = 1.
  control.hudControl.setSpeed = 35.
  buttons = []
  receipts = []
  for tick in range(150):
    now = 1_000_000_000 + tick * 10_000_000
    frames = qualified_frames(packer, tick % 4)
    frames = [(a, bytes(d) + b'\x00\x00' if a == 0x17D else d, b) for a, d, b in frames]
    frames += [packer.make_can_msg('BCMBlindSpotMonitor', 0, {}),
               packer.make_can_msg('ASCMLKASteeringCmd', 2, {'RollingCounter': tick % 4}),
               packer.make_can_msg('AEBCmd', 2, {}), packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {})]
    safety.set_timer(now // 1000)
    for frame in frames:
      assert native('rx', frame, now // 1000)
    safety.safety_tick()
    state = ci.update([(now, frames + receipts)])
    if tick > 30:
      assert state.canValid and not state.accFaulted and state.cruiseState.enabled
      assert safety.safety_config_valid()
    _, outgoing = ci.apply(control.as_reader(), now)
    receipts = [(a, d, 128) for a, d, b in outgoing if a == 0x180 and b == 0]
    for frame in outgoing:
      assert native('tx', frame, now // 1000), (tick, frame)
      if frame[0] == 0x1E1:
        buttons.append(frame)
    assert all(a not in (0x315, 0x2CB, 0x2CD, 0x200) for a, _, _ in outgoing)
  assert bool(buttons) == (not disabled)


def test_sierra_main_already_on_reaches_actual_lateral_callers():
  from openpilot.starpilot.aol.intent import read_settings
  from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
  with OpenpilotPrefix():
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    try:
      sd, _, step = case._authority_callers(False, cp=params(), genuine_events=True)
      sd.aol_settings = read_settings(sd.params)
      axis, state, command = step(0, native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive
      assert not axis.longitudinalActive and not command.longActive and not state.enabled
    finally:
      case.doCleanups()
