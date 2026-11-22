import math
import unittest

from opendbc.car import DT_CTRL, CanData, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.fingerprints import FW_VERSIONS
from opendbc.car.fw_versions import FW_QUERY_CONFIGS
from opendbc.car.interfaces import CarInterfaceBase, get_interface_attr
from opendbc.car.values import PLATFORMS
from opendbc.car.volvo.cma_carcontroller import CarControllerCMA
from opendbc.car.volvo.cma_carstate import CarStateCMA
from opendbc.car.volvo.eucd_carcontroller import CarControllerEUCD
from opendbc.car.volvo.eucd_carstate import CarStateEUCD
from opendbc.car.volvo.values import CAR as VOLVO
from opendbc.testing import Fuzzy, fuzzy_test

ALL_ECUS = tuple(sorted({ecu for ecus in FW_VERSIONS.values() for ecu in ecus} |
                        {ecu for config in FW_QUERY_CONFIGS.values() for ecu in config.extra_ecus}))

ALL_REQUESTS = tuple(sorted({tuple(r.request) for config in FW_QUERY_CONFIGS.values() for r in config.requests}))

# From panda/python/__init__.py
DLC_TO_LEN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


def get_fuzzy_car_interface(car_name: str, fuzzy: Fuzzy) -> CarInterfaceBase:
  fingerprint = dict(fuzzy.list(lambda: (fuzzy.integer(0, 0x800), fuzzy.choice(DLC_TO_LEN))))
  # reduce search space by duplicating CAN fingerprints across all buses
  fingerprints = dict.fromkeys(range(7), fingerprint)

  def generate_car_fw():
    ecu, address, sub_address = fuzzy.choice(ALL_ECUS)
    return structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=sub_address or 0,
                                  request=fuzzy.choice(ALL_REQUESTS))

  # initialize car interface
  CarInterface = interfaces[car_name]
  car_params = CarInterface.get_params(car_name, fingerprints, fuzzy.list(generate_car_fw),
                                       alpha_long=fuzzy.boolean(), is_release=False, docs=False)
  return CarInterface(car_params)


def _make_car_test(car_name):
  @fuzzy_test(max_examples=15)
  def test(self, fuzzy):
    car_interface = get_fuzzy_car_interface(car_name, fuzzy)
    car_params = car_interface.CP.as_reader()

    assert car_params.mass > 1
    assert car_params.wheelbase > 0
    # centerToFront is center of gravity to front wheels, assert a reasonable range
    assert car_params.wheelbase * 0.3 < car_params.centerToFront < car_params.wheelbase * 0.7
    assert car_params.maxLateralAccel > 0

    # Longitudinal sanity checks
    assert len(car_params.longitudinalTuning.kiV) == len(car_params.longitudinalTuning.kiBP)

    # Lateral sanity checks
    if car_params.steerControlType not in (structs.CarParams.SteerControlType.angle, structs.CarParams.SteerControlType.curvature):
      tune = car_params.lateralTuning
      if tune.which() == 'pid':
        assert not math.isnan(tune.pid.kf) and tune.pid.kf >= 0
        assert len(tune.pid.kpV) > 0 and len(tune.pid.kpV) == len(tune.pid.kpBP)
        assert len(tune.pid.kiV) > 0 and len(tune.pid.kiV) == len(tune.pid.kiBP)

      elif tune.which() == 'torque':
        assert not math.isnan(tune.torque.latAccelFactor) and tune.torque.latAccelFactor > 0
        assert not math.isnan(tune.torque.friction) and tune.torque.friction > 0

    # Run car interface
    # TODO: generate random messages
    now_nanos = 0
    CC = structs.CarControl().as_reader()
    for _ in range(10):
      car_interface.update([])
      car_interface.apply(CC, now_nanos)
      now_nanos += DT_CTRL * 1e9  # 10 ms

    CC = structs.CarControl()
    CC.enabled = True
    CC.latActive = True
    CC.longActive = True
    CC = CC.as_reader()
    for _ in range(10):
      car_interface.update([])
      car_interface.apply(CC, now_nanos)
      now_nanos += DT_CTRL * 1e9  # 10ms

    # Test radar interface
    radar_interface = car_interface.RadarInterface(car_params)
    assert radar_interface

    # Run radar interface once
    radar_interface.update([])
    if not car_params.radarUnavailable and radar_interface.rcp is not None and \
       hasattr(radar_interface, '_update') and hasattr(radar_interface, 'trigger_msg'):
      radar_interface._update([radar_interface.trigger_msg])

    # Test radar fault
    if not car_params.radarUnavailable and radar_interface.rcp is not None:
      cans = [(0, [CanData(0, b'', 0) for _ in range(5)])]
      rr = radar_interface.update(cans)
      assert rr is None or len(rr.errors) > 0

  return test


def _make_optional_hardware_test(car_name):
  def test(self):
    # Identification can succeed before optional ECUs or messages are available.
    for alpha_long in (False, True):
      for is_release in (False, True):
        with self.subTest(alpha_long=alpha_long, is_release=is_release):
          fingerprint = gen_empty_fingerprint()
          interface_type = interfaces[car_name]
          params = interface_type.get_params(car_name, fingerprint, [], alpha_long, is_release, docs=False)
          self.assertEqual(params.carFingerprint, car_name)
          self.assertEqual(fingerprint, gen_empty_fingerprint())
          interface = interface_type(params)
          if car_name in (VOLVO.VOLVO_XC40_RECHARGE, VOLVO.VOLVO_S60_RECHARGE, VOLVO.POLESTAR_2):
            self.assertIsInstance(interface.CS, CarStateCMA)
            self.assertIsInstance(interface.CC, CarControllerCMA)
          elif car_name == VOLVO.VOLVO_V60:
            self.assertIsInstance(interface.CS, CarStateEUCD)
            self.assertIsInstance(interface.CC, CarControllerEUCD)
          else:
            self.assertIsInstance(interface.CS, interface_type.CarState)
            self.assertIsInstance(interface.CC, interface_type.CarController)

  return test


class TestCarInterfaces(unittest.TestCase):
  def test_interface_attrs(self):
    """Asserts basic behavior of interface attribute getter"""
    num_brands = len(get_interface_attr('CAR'))
    assert num_brands >= 12

    # Should return value for all brands when not combining, even if attribute doesn't exist
    ret = get_interface_attr('FAKE_ATTR')
    assert len(ret) == num_brands

    # Make sure we can combine dicts
    ret = get_interface_attr('DBC', combine_brands=True)
    assert len(ret) >= 160

    # We don't support combining non-dicts
    ret = get_interface_attr('CAR', combine_brands=True)
    assert len(ret) == 0

    # If brand has None value, it shouldn't return when ignore_none=True is specified
    none_brands = {b for b, v in get_interface_attr('FINGERPRINTS').items() if v is None}
    assert len(none_brands) >= 1

    ret = get_interface_attr('FINGERPRINTS', ignore_none=True)
    none_brands_in_ret = none_brands.intersection(ret)
    assert len(none_brands_in_ret) == 0, f'Brands with None values in ignore_none=True result: {none_brands_in_ret}'


for car_name in sorted(PLATFORMS):
  setattr(TestCarInterfaces, f'test_car_interfaces_{car_name}', _make_car_test(car_name))
  setattr(TestCarInterfaces, f'test_optional_hardware_absent_{car_name}', _make_optional_hardware_test(car_name))
