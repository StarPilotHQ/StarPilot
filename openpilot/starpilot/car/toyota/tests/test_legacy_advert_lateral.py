"""Typed predicate boundaries; actual native permissions are tested by the joined callers."""
from dataclasses import replace
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota import legacy_advert_filter
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests import test_legacy_sdsu_lateral as original
from openpilot.starpilot.car.toyota.tests.test_rav4h_caller_native import firmware

CARS = (CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_COROLLA, CAR.LEXUS_IS)
FAULTS = ('stale_source', 'future_source', 'future_intent', 'stale_native', 'wrong_session', 'missing_inventory',
          'rx_invalid', 'native_fault', 'wrong_native_word', 'missing_latch', 'paused_lateral', 'missing_settings',
          'main_off', 'reverse', 'door', 'can_invalid', 'can_timeout', 'temporary_eps', 'permanent_eps', 'acc_fault',
          'stock_aeb', 'above_minimum', 'wrong_profile')


def bind_evidence(candidate, monkeypatch):
  baseline = original.evidence

  def evidence():
    previous, cs, intent, native = baseline()
    fp = gen_empty_fingerprint()
    fp[0][0x2FF] = 4
    cp = CarInterface.get_params(candidate, fp, firmware(candidate), False, False, False)
    if candidate == CAR.TOYOTA_HIGHLANDER:
      assert legacy_advert_filter.prepare_stock(cp)
    cp.alternativeExperience = previous.alternativeExperience
    slot = replace(native.pandaInventory[0], safetyParam=cp.safetyConfigs[0].safetyParam)
    native = replace(native, safetyParam=slot.safetyParam, pandaInventory=(slot,))
    return cp, cs, intent, native

  monkeypatch.setattr(original, 'evidence', evidence)


@pytest.mark.parametrize('candidate', CARS)
@pytest.mark.parametrize('fault', FAULTS)
def test_advertisement_keeps_every_low_speed_source_native_veto(candidate, fault, monkeypatch):
  bind_evidence(candidate, monkeypatch)
  original.test_low_speed_exception_retains_source_and_native_denials(fault)


@pytest.mark.parametrize('candidate', CARS)
@pytest.mark.parametrize('requested,permission,expected', ((False, False, True), (True, True, True), (True, False, False)))
def test_advertisement_only_requests_or_uses_current_native_lateral(candidate, requested, permission, expected, monkeypatch):
  bind_evidence(candidate, monkeypatch)
  original.test_low_speed_only_admits_request_or_actual_lateral_grant(requested, permission, expected)
