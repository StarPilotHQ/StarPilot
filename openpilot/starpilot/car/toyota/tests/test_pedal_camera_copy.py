"""Camera copies preserve exact primary pedal enrollment and current leases."""
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota import pedal, pedal_composite
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import ROWS, sources
from openpilot.starpilot.car.toyota.tests.test_pedal_caller_native import firmware


@pytest.mark.parametrize('name,topology,rack', ROWS)
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('stock', (False, True))
def test_every_composite_camera_copy_keeps_exact_configuration(name, topology, rack, alpha, stock):
  identity = getattr(CAR, name)
  fp, fw = sources(identity, topology, rack)
  fp[0][0x201] = 6
  canonical = CarInterface.get_params(identity, fp, fw, alpha, False, False)
  fp[2][0x201] = 6
  mirrored = CarInterface.get_params(identity, fp, fw, alpha, False, False)
  assert pedal_composite.configured(mirrored)
  if stock:
    assert pedal_composite.prepare_stock(canonical) and pedal_composite.prepare_stock(mirrored)
  assert mirrored.to_dict() == canonical.to_dict()


@pytest.mark.parametrize('identity', sorted(pedal.CAMERA_PEDAL_CARS | pedal.RADAR_PEDAL_CARS))
@pytest.mark.parametrize('alpha', (False, True))
def test_standalone_camera_copy_keeps_exact_configuration(identity, alpha):
  fp = gen_empty_fingerprint()
  fp[0][0x201] = 6
  fw = firmware(identity)
  canonical = CarInterface.get_params(identity, fp, fw, alpha, False, False)
  fp[2][0x201] = 6
  mirrored = CarInterface.get_params(identity, fp, fw, alpha, False, False)
  assert mirrored.to_dict() == canonical.to_dict()


@pytest.mark.parametrize('fp', ({0: {}, 2: {0x201: 6}}, {0: {0x201: 6}, 2: {0x201: 5}},
                               {0: {0x201: 6}, 1: {0x201: 6}}, {0: {0x201: 5}, 2: {0x201: 6}},
                               {0: {0x201: 6}, 4: {0x201: 6}}))
def test_copy_is_not_a_primary_or_second_pedal(fp):
  assert not pedal.primary_pedal_hardware(fp)
