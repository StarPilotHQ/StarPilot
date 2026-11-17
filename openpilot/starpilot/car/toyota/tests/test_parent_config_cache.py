"""Cached parent admission must follow exact mutable factory configuration."""
import pytest
from opendbc.car.toyota import advert_filter, rav4h_filter, prius_longitudinal, prius_advert_filter, pedal_composite
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources

ROWS = (
  (advert_filter.configured, advert_filter, CAR.TOYOTA_RAV4_TSS2, '2FF4', 'catalog'),
  (rav4h_filter.configured, rav4h_filter, CAR.TOYOTA_RAV4H, '2FF8', 'catalog'),
  (prius_longitudinal.configured, prius_longitudinal, CAR.TOYOTA_PRIUS_RETROFIT, '2FF8', 'EPS73_8965B47070'),
  (prius_longitudinal.eps73_status_configured, prius_longitudinal, CAR.TOYOTA_PRIUS_RETROFIT, '2FF8', 'EPS73_8965B47070'),
  (prius_advert_filter.configured, prius_advert_filter, CAR.TOYOTA_PRIUS_RETROFIT, '2FF4', 'EPS73_8965B47070'),
)


@pytest.mark.parametrize('check,owner,identity,topology,rack', ROWS)
@pytest.mark.parametrize('pedal', (False, True))
@pytest.mark.parametrize('stock', (False, True))
def test_original_parent_admission_and_mutable_aliases(check, owner, identity, topology, rack, pedal, stock):
  fp, fw = sources(identity, topology, rack)
  if pedal:
    fp[0][0x201] = 6
  cp = CarInterface.get_params(identity, fp, fw, True, False, False)
  if stock:
    assert (pedal_composite if pedal else owner).prepare_stock(cp)
  assert check(cp) and check.__wrapped__(cp)
  alias = cp.as_reader()
  assert check(alias)
  for field, value in (
    ('brand', 'other'), ('carFingerprint', 'unknown'), ('flags', 0), ('passive', True),
    ('dashcamOnly', True), ('notCar', True), ('pcmCruise', False), ('secOcRequired', True),
    ('openpilotLongitudinalControl', not cp.openpilotLongitudinalControl),
    ('alternativeExperience', 999), ('autoResumeSng', not cp.autoResumeSng),
    ('steerControlType', 'angle'), ('carVin', 'complete-key mutation'),
  ):
    mutated = cp.as_reader().as_builder()
    reader = mutated.as_reader()
    assert check(mutated)
    setattr(mutated, field, value)
    assert check(mutated) == check(reader) == check.__wrapped__(mutated)
  for field, value in (('safetyParam', 0), ('safetyModel', 'noOutput')):
    mutated = cp.as_reader().as_builder()
    assert check(mutated)
    setattr(mutated.safetyConfigs[0], field, value)
    assert not check(mutated) and not check.__wrapped__(mutated)
