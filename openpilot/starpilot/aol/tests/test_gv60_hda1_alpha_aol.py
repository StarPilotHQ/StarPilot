from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.torque_ev_scc import eligible
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.car.hyundai.aol import create_intent, native_accepts_cp, policy_for
from openpilot.starpilot.ui.wheel_feature import WheelFeature


def gv60(*, alpha=True, lka=False, alternate=False):
  fp = gen_empty_fingerprint()
  bus = int(lka)
  fp[bus].update({0x1AA if alternate else 0x1CF: 16 if alternate else 8, 0x130: 16, 0x35: 32})
  if lka:
    fp[2][0x50] = 16
  fw = [structs.CarParams.CarFw(ecu='adas')] if lka else []
  return CarInterface.get_params(CAR.GENESIS_GV60_EV_1ST_GEN, fp, fw, alpha, False, False)


@pytest.mark.parametrize('alpha', [False, True])
def test_factory_buttons_and_saved_aol_off_are_preserved(alpha):
  cp = gv60(alpha=alpha)
  before = cp.to_dict()
  assert cp.safetyConfigs[-1].safetyParam == (0xD if alpha else 9)
  policy = policy_for(cp)
  assert policy.settings_supported and policy.runtime_supported
  owner = SimpleNamespace(vehicle_params=lambda: cp, configuration_vehicle=lambda: False,
                          configuration_longitudinal=lambda: False, longitudinal_available=lambda: alpha)
  for key in ('LKASButtonControl', 'MainCruiseButtonControl'):
    assert 'Toggle AOL' in WheelFeature(owner).choices(key)
  disabled = create_intent(cp, AolSettings(False, 0., 0, 10, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  disabled.update(cs, now_ns=1, fault_active=False)
  assert not disabled.allowed_latch
  assert cp.to_dict() == before


@pytest.mark.parametrize('button', ['lkas', 'mainCruise'])
def test_marked_alpha_button_arm_retains_intent_after_cancel_and_brake(button):
  cp = gv60()
  assert qualified_long(cp) and not eligible(cp)
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert cp.safetyConfigs[-1].safetyParam == 0x80D and cp.alternativeExperience == 32
  assert qualified_long(cp, marked_only=True) and not eligible(cp)
  assert native_accepts_cp(cp, int(cp.safetyConfigs[-1].safetyModel.raw), 0x80D)
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


@pytest.mark.parametrize('lka,alternate', [(True, False), (True, True), (False, True)])
def test_unverified_alpha_layouts_do_not_gain_aol(lka, alternate):
  assert not qualified_long(gv60(lka=lka, alternate=alternate))


def test_bad_identity_health_word_or_experience_cannot_admit():
  cp = gv60()
  for field, value in (('carFingerprint', CAR.KIA_SELTOS), ('passive', True), ('dashcamOnly', True),
                       ('notCar', True), ('pcmCruise', True), ('alternativeExperience', 32)):
    bad = cp.as_reader().as_builder()
    setattr(bad, field, value)
    assert not qualified_long(bad)
  for word in (0x80F, 0x82D, 0x1815, 0x480D):
    bad = cp.as_reader().as_builder()
    bad.safetyConfigs[-1].safetyParam = word
    bad.alternativeExperience = 32
    assert not qualified_long(bad)


def test_aol_admission_preserves_full_engagement_controller_packets():
  from opendbc.can import CANPacker
  from opendbc.car import Bus
  from opendbc.car.hyundai.tests.test_torque_long_parser_layouts import frames
  from opendbc.car.hyundai.values import DBC

  cp = gv60()
  marked = cp.as_reader().as_builder()
  policy = policy_for(marked)
  marked.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  marked.alternativeExperience |= policy.alternative_experience_addition
  baseline, candidate = CarInterface(cp), CarInterface(marked)
  assert cp.lateralTuning.to_dict() == marked.lateralTuning.to_dict()
  assert cp.longitudinalTuning.to_dict() == marked.longitudinalTuning.to_dict()
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(1, 301):
    for ci in (baseline, candidate):
      ci.update(frames(ci, packer, tick))
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.actuators.torque = ((tick % 40) - 20) / 50
    cc.actuators.accel = ((tick % 30) - 15) / 20
    cc.actuators.longControlState = 'pid'
    now = 10_000_000_000 + tick * 10_000_000
    old_output, old_packets = baseline.apply(cc.as_reader(), now)
    new_output, new_packets = candidate.apply(cc.as_reader(), now)
    assert old_output.to_dict() == new_output.to_dict()
    assert old_packets == new_packets
