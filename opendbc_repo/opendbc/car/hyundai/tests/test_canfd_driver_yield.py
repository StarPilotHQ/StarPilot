import pytest

from opendbc.can.parser import CANParser
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, CarControllerParams, HyundaiFlags, HyundaiSafetyFlags
from opendbc.car.lateral import apply_driver_steer_torque_limits

TORQUE_CANFD = tuple(car for car in CAR if car.config.flags & HyundaiFlags.CANFD and
                     not car.config.flags & HyundaiFlags.CANFD_ANGLE_STEERING)


@pytest.mark.parametrize('car', TORQUE_CANFD)
@pytest.mark.parametrize('driver', (-355., 355.))
@pytest.mark.parametrize('speed', (3.5, 30.))
def test_factory_torque_canfd_yields_to_strong_opposing_driver_torque(car, driver, speed):
  cp = CarInterface.get_non_essential_params(car)
  limits = CarControllerParams(cp, speed)
  previous = 0
  for _ in range(100):
    previous = apply_driver_steer_torque_limits(409 if driver < 0 else -409, previous, driver, limits)
    assert previous == 0


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('driver', (-355., 355.))
def test_niro_actual_controller_and_wire_yield_identically_with_stock_and_alpha(alpha, driver):
  fp = gen_empty_fingerprint()
  fp[0].update({0x1CF: 8, 0x35: 32, 0x130: 16, 0x175: 24, 0xA0: 24, 0xEA: 24})
  cp = CarInterface.get_params(CAR.KIA_NIRO_EV_2ND_GEN, fp, [], alpha, False, False)
  cp.openpilotLongitudinalControl = alpha
  cp.pcmCruise = not alpha
  cp.safetyConfigs[-1].safetyParam |= int(HyundaiSafetyFlags.LONG) if alpha else 0
  cc = structs.CarControl(latActive=True)
  cc.actuators.torque = 1. if driver < 0 else -1.
  state = CarState(cp)
  state.out = structs.CarState(vEgo=3.5, vEgoRaw=3.5, canValid=True, steeringTorque=driver, steeringPressed=True,
                              gearShifter=structs.CarState.GearShifter.drive)
  state.out.cruiseState.available = True
  state.is_metric = True
  controller = CarController(DBC[CAR.KIA_NIRO_EV_2ND_GEN], cp)
  parser = CANParser(DBC[CAR.KIA_NIRO_EV_2ND_GEN]['pt'], [('LFA', 0)], controller.CAN.ECAN)
  for tick in range(40):
    stamp = 1_000_000_000 + tick * 10_000_000
    actuators, packets = controller.update(cc.as_reader(), state, stamp)
    packet = next(p for p in packets if p[0] == 0x12A)
    parser.update([stamp, [packet]])
    assert parser.can_valid
    assert actuators.torqueOutputCan == 0
    assert parser.vl['LFA']['StrTqReqVal'] == 0
    assert parser.vl['LFA']['ActToiSta'] == 1


@pytest.mark.parametrize('car', TORQUE_CANFD)
def test_torque_canfd_normal_assistance_and_other_parameters_are_unchanged(car):
  cp = CarInterface.get_non_essential_params(car)
  limits = CarControllerParams(cp, 3.5)
  no_driver = light_driver = 0
  for _ in range(100):
    no_driver = apply_driver_steer_torque_limits(350, no_driver, 0., limits)
    light_driver = apply_driver_steer_torque_limits(350, light_driver, -50., limits)
    assert no_driver == light_driver
  assert limits.STEER_MAX == 409
  assert limits.STEER_DRIVER_MULTIPLIER == 2 and limits.STEER_DRIVER_FACTOR == 1
  assert (limits.STEER_DELTA_UP, limits.STEER_DELTA_DOWN) == ((2, 3) if car == CAR.KIA_CARNIVAL_HEV_4TH_GEN else (10, 8))
  assert limits.STEER_THRESHOLD == 250


@pytest.mark.parametrize('car', (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9, CAR.HYUNDAI_SONATA, CAR.KIA_NIRO_EV))
def test_angle_and_classic_can_driver_limits_are_unchanged(car):
  cp = CarInterface.get_non_essential_params(car)
  limits = CarControllerParams(cp)
  angle = bool(cp.flags & HyundaiFlags.CANFD_ANGLE_STEERING)
  assert limits.STEER_DRIVER_ALLOWANCE == (250 if angle else 50)
  assert limits.STEER_THRESHOLD == (175 if angle else 150)
