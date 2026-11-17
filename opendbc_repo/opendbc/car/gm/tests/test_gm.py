import unittest

from opendbc.car.gm.fingerprints import FINGERPRINTS
from opendbc.car.gm.values import CAR, CAMERA_ACC_CAR, NO_ACC_BOLT_CAR, GM_RX_OFFSET
from opendbc.testing import parameterized

CAMERA_DIAGNOSTIC_ADDRESS = 0x24b


class TestGMFingerprint(unittest.TestCase):
  @parameterized("car_model, fingerprints", FINGERPRINTS.items())
  def test_can_fingerprints(self, car_model, fingerprints):
    assert len(fingerprints) > 0

    assert all(len(finger) for finger in fingerprints)

    # The camera can sometimes be communicating on startup
    if car_model == CAR.CHEVROLET_TRAX:
      from opendbc.car.gm.tests.test_trax_fingerprint import RECORDED
      assert fingerprints == [{int(address): size for address, size in RECORDED['bus0'].items()}]
    elif car_model in CAMERA_ACC_CAR - NO_ACC_BOLT_CAR:
      for finger in fingerprints:
        for required_addr in (CAMERA_DIAGNOSTIC_ADDRESS, CAMERA_DIAGNOSTIC_ADDRESS + GM_RX_OFFSET):
          assert finger.get(required_addr) == 8, required_addr
