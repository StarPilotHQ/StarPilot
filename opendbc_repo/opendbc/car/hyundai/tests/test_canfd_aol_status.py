import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified, qualified_long
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.torque_ev_startup import candidate
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags


STOCK_CARS = (CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_IONIQ_5_N,
              CAR.KIA_NIRO_EV_2ND_GEN, CAR.KIA_EV6, CAR.GENESIS_GV60_EV_1ST_GEN,
              CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.HYUNDAI_SONATA_2024, CAR.KIA_SPORTAGE_5TH_GEN)


def stock_params(car, topology):
  fp = gen_empty_fingerprint()
  lka = topology != 'hda1'
  if lka:
    fp[2].update({0x110: 32, 0x362: 32} if topology == 'hda2_alt' else {0x50: 16, 0x2A4: 24})
  fp[1 if lka else 0][0x1CF] = 8
  if car == CAR.KIA_SPORTAGE_5TH_GEN:
    fp[0][0xFA] = 8
  return CarInterface.get_params(car, fp, [], False, False, False)


def check_controller(cp):
  ci = CarInterface(cp)
  ci.update([])
  controller, cs = ci.CC, ci.CS
  lka = bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
  message = 'LKAS_ALT' if cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else 'LKAS' if lka else 'LFA'
  address = 0x110 if message == 'LKAS_ALT' else 0x50 if lka else 0x12A
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [(message, 0)], controller.CAN.ACAN if lka else controller.CAN.ECAN)
  cluster = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFAHDA_CLUSTER', 0)], controller.CAN.ECAN)
  command = structs.CarControl.new_message()
  cs.out = structs.CarState.new_message(vEgo=5., vEgoRaw=5., gearShifter=structs.CarState.GearShifter.drive,
                                       cruiseState={'available': True})
  states = ((False, False), (False, True), (True, True), (True, False), (False, False))
  for index, (enabled, lateral) in enumerate(states):
    command.enabled, command.latActive = enabled, lateral
    cs.out.steerFaultPermanent = index == 4
    controller.frame = index * 5
    packets = controller.create_canfd_msgs(lateral, 40 if lateral else 0, 60., 0., False,
                                           command.hudControl, cs, command, 1_000_000_000 + index * 50_000_000)
    steering = [packet for packet in packets if packet[0] == address]
    assert len(steering) == 1
    decoder.update([(1_000_000_000 + index * 50_000_000, steering)])
    actual = decoder.vl[message]
    assert actual['LKA_SysIndReq'] == (2 if enabled or lateral else 1)
    assert actual['ActToiSta'] == int(lateral)
    assert actual['StrTqReqVal'] == (40 if lateral else 0)
    icons = [packet for packet in packets if packet[0] == 0x1E0]
    ccnc_display = bool(cp.flags & HyundaiFlags.CCNC and not lka)
    assert len(icons) == int((not lka or cp.openpilotLongitudinalControl) and not ccnc_display)
    if icons:
      cluster.update([(1_000_000_000 + index * 50_000_000, icons)])
      assert cluster.vl['LFAHDA_CLUSTER']['LFA_ICON'] == (2 if enabled or lateral else 3 if index == 4 else 0)
      assert cluster.vl['LFAHDA_CLUSTER']['HDA_ICON'] == int(enabled)


@pytest.mark.parametrize('car', STOCK_CARS)
@pytest.mark.parametrize('topology', ('hda1', 'hda2', 'hda2_alt'))
def test_shared_stock_aol_status_tracks_lateral_without_cruise(car, topology):
  cp = stock_params(car, topology)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  assert qualified(cp, marked_only=True)
  check_controller(cp)


@pytest.mark.parametrize('car', (CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_KONA_EV_2ND_GEN))
@pytest.mark.parametrize('topology', ('hda2', 'hda2_alt'))
def test_alpha_long_uses_the_same_lateral_status(car, topology):
  cp = candidate(stock_params(car, topology), requested=True, is_release=False)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  assert qualified_long(cp, marked_only=True)
  check_controller(cp)


def test_passive_and_angle_profiles_do_not_gain_torque_aol_status():
  for passive in (True, False):
    cp = stock_params(CAR.HYUNDAI_KONA_EV_2ND_GEN if passive else CAR.HYUNDAI_IONIQ_5_PE, 'hda2_alt')
    cp.passive = passive
    ci = CarInterface(cp)
    assert not getattr(ci.CC, 'canfd_torque_aol_status', getattr(ci.CC, 'ev6_aol_status', False))
