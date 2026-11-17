"""Only finalized GM configuration is cached; mutable CP aliases remain authoritative."""
from types import SimpleNamespace

import unittest

from opendbc.car import structs
from opendbc.car.gm.steering_authority import monitored_profile
from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params
from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.tests.test_volt_transitions import volt_ascm_pedal_params, volt_sdgm_pedal_params
from opendbc.car.gm.values import CAR


def profiles():
  for alpha in (False, True):
    for c9 in (False, True):
      yield ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=alpha, sascm=True, radar=True, accelerator=not c9)
      yield sdgm_params(alpha=alpha, brake_c9=c9, radar=True)
  yield volt_ascm_pedal_params(alpha=True, sascm=True)
  yield volt_sdgm_pedal_params(alpha=True, sascm=True)
  yield bolt_params(CAR.CHEVROLET_BOLT_CC_2017)


class TestSteeringProfileCache(unittest.TestCase):
  def test_mutable_configuration_and_reader_alias_match_original(self):
    for cp in profiles():
      with self.subTest(identity=cp.carFingerprint, word=cp.safetyConfigs[0].safetyParam):
        self._check_mutable_configuration(cp)

  def _check_mutable_configuration(self, cp):
    original = monitored_profile.__wrapped__
    for field, value in (('passive', True), ('dashcamOnly', True), ('notCar', True),
                         ('pcmCruise', True), ('openpilotLongitudinalControl', False),
                         ('alphaLongitudinalAvailable', False), ('radarUnavailable', True),
                         ('flags', 1 << 31), ('brand', 'hyundai'), ('carFingerprint', 'CHEVROLET_BOLT_CC_2017'),
                         ('networkLocation', 'gateway'), ('transmissionType', 'automatic'),
                         ('alternativeExperience', 0x1234), ('mass', 2100.123), ('minSteerSpeed', 7.777)):
      builder = cp.copy()
      alias = builder.as_reader()
      written = builder._is_written
      monitored_profile.cache_clear()
      assert monitored_profile(builder) == original(builder)
      setattr(builder, field, value)
      assert monitored_profile(alias) == original(alias)
      assert monitored_profile(builder) == original(builder)
      assert builder._is_written == written
      if getattr(cp, field) != value:
        assert monitored_profile.cache_info().misses == 2
    for count in (0, 2):
      builder = cp.copy()
      builder.safetyConfigs = [cp.safetyConfigs[0].to_dict()] * count
      assert monitored_profile(builder) == original(builder)


  def test_cache_is_bounded_and_reuses_warm_results(self):
    cp = next(profiles())
    monitored_profile.cache_clear()
    for index in range(12):
      cp.mass = 2100 + index
      assert monitored_profile(cp) == monitored_profile.__wrapped__(cp)
    assert monitored_profile.cache_info().currsize == 8
    before = monitored_profile.cache_info()
    assert monitored_profile(cp) == monitored_profile(cp.as_reader())
    assert monitored_profile.cache_info().hits == before.hits + 2


  def test_oversized_and_malformed_inputs_preserve_original_fallback(self):
    cp = next(profiles())
    cp.carVin = 'X' * 70000
    monitored_profile.cache_clear()
    assert monitored_profile(cp) == monitored_profile.__wrapped__(cp)
    assert monitored_profile.cache_info().currsize == 0
    for value in (None, object(), SimpleNamespace(), structs.CarState.new_message()):
      assert monitored_profile(value) == monitored_profile.__wrapped__(value)

    class Broken:
      @property
      def brand(self):
        raise RuntimeError('original guard exception')

    for check in (monitored_profile, monitored_profile.__wrapped__):
      with self.assertRaisesRegex(RuntimeError, 'original guard exception'):
        check(Broken())
