from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.car.hyundai.aol import create_intent, native_accepts_cp, policy_for
from openpilot.starpilot.ui.wheel_feature import WheelFeature


def sportage(*, hybrid=False, alternate=False, lka=0, alpha=True):
  fp = gen_empty_fingerprint()
  bus = 1 if lka else 0
  if lka:
    fp[2][0x110 if lka == 2 else 0x50] = 32 if lka == 2 else 16
  fp[bus].update({0x1aa if alternate else 0x1cf: 16 if alternate else 8, 0x130: 32, 0x35: 32})
  if hybrid:
    fp[bus][0xFA] = 8
  fw = [structs.CarParams.CarFw(ecu='adas')] if lka else []
  return CarInterface.get_params(CAR.KIA_SPORTAGE_5TH_GEN, fp, fw, alpha, False, False)


@pytest.mark.parametrize('hybrid', [False, True])
@pytest.mark.parametrize('layout', [(0, False), (0, True), (1, False), (2, False)])
@pytest.mark.parametrize('alpha', [False, True])
def test_sportage_factory_wheel_choices_and_physical_latch(hybrid, layout, alpha):
  lka, alternate = layout
  cp = sportage(hybrid=hybrid, alternate=alternate, lka=lka, alpha=alpha)
  assert cp.openpilotLongitudinalControl == alpha
  policy = policy_for(cp)
  assert policy.settings_supported and policy.runtime_supported
  owner = SimpleNamespace(vehicle_params=lambda: cp, configuration_vehicle=lambda: False,
                          configuration_longitudinal=lambda: False, longitudinal_available=lambda: alpha)
  for key in ('LKASButtonControl', 'MainCruiseButtonControl'):
    assert 'Toggle AOL' in WheelFeature(owner).choices(key)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert native_accepts_cp(cp, int(cp.safetyConfigs[-1].safetyModel.raw), cp.safetyConfigs[-1].safetyParam)
  if alpha:
    assert qualified_long(cp, marked_only=True)
  for button in ('lkas', 'mainCruise'):
    intent = create_intent(cp, AolSettings(True, 0., 9, 9, (0, 0, 0), (0, 0, 0)))
    cs = structs.CarState(canValid=True, gearShifter='drive')
    cs.cruiseState.available = True
    intent.update(cs, now_ns=1, fault_active=False)
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=True)]
    intent.update(cs, now_ns=2, fault_active=False)
    assert intent.allowed_latch
    cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
    intent.update(cs, now_ns=3, fault_active=False)
    assert intent.allowed_latch
    cs.buttonEvents = []
    cs.brakePressed = True
    intent.update(cs, now_ns=4, fault_active=False)
    assert intent.allowed_latch
    cs.canValid = False
    intent.update(cs, now_ns=5, fault_active=False)
    assert not intent.output(cs)[0]


def test_sportage_alpha_rejects_wrong_identity_word_and_experience():
  cp = sportage()
  assert qualified_long(cp)
  for field, value in (('carFingerprint', CAR.KIA_SELTOS), ('passive', True), ('dashcamOnly', True),
                       ('notCar', True), ('pcmCruise', True), ('alternativeExperience', 32)):
    bad = cp.as_reader().as_builder()
    setattr(bad, field, value)
    assert not qualified_long(bad)
  for word in (0x80d, 0x80f, 0x830, 0x180c, 0x480c):
    bad = cp.as_reader().as_builder()
    bad.safetyConfigs[-1].safetyParam = word
    bad.alternativeExperience = 32
    assert not qualified_long(bad)


@pytest.mark.parametrize('hybrid', [False, True])
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('alpha', [False, True])
def test_sportage_lateral_only_uses_active_status_without_changing_normal_commands(hybrid, alternate, alpha):
  from opendbc.can import CANParser
  from opendbc.car import Bus
  from opendbc.car.hyundai.values import DBC

  cp = sportage(hybrid=hybrid, alternate=alternate, alpha=alpha)
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  ci = CarInterface(cp)
  ci.update([])
  parser = CANParser(DBC[CAR.KIA_SPORTAGE_5TH_GEN][Bus.pt], [('LFA', 0)], 0)
  before = cp.to_dict()
  for enabled, lateral, expected in ((False, False, 1), (False, True, 2), (True, True, 2), (True, False, 2)):
    cc = structs.CarControl(enabled=enabled, latActive=lateral)
    stamp = 10_000_000_000 + ci.CC.frame * 10_000_000
    _, frames = ci.CC.update(cc.as_reader(), ci.CS, stamp)
    parser.update((stamp, frames))
    assert parser.vl['LFA']['LKA_SysIndReq'] == expected
    assert parser.vl['LFA']['Damping_Gain'] == 100
    assert cp.to_dict() == before
