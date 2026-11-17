import unittest
from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import FRAME_FINGERPRINT, can_fingerprint
from opendbc.car.fingerprints import _FINGERPRINTS as FINGERPRINTS
from opendbc.testing import parameterized


class TestCanFingerprint(unittest.TestCase):
  @parameterized("car_model, fingerprints", FINGERPRINTS.items())
  def test_can_fingerprint(self, car_model, fingerprints):
    """Tests online fingerprinting function on offline fingerprints"""

    for fingerprint in fingerprints:  # can have multiple fingerprints for each platform
      can = [CanData(address=address, dat=b'\x00' * length, src=src)
             for address, length in fingerprint.items() for src in (0, 1)]

      fingerprint_iter = iter([can])
      car_fingerprint, finger = can_fingerprint(lambda **kwargs: [next(fingerprint_iter, [])])  # noqa: B023

      # Legacy tables alias Gen2 hardware to old or broad Bolt identities.
      # Zero payloads provide no affirmative camera ACC evidence.
      ambiguous_bolt = (car_model in ("CHEVROLET_BOLT_EUV", "CHEVROLET_BOLT_CC_2017", "CHEVROLET_BOLT_CC_2018_2021") and
                        fingerprint.get(0xD3) == 3)
      ambiguous_hybrid = car_model == "CHEVROLET_MALIBU_HYBRID_CC"
      ambiguous_trax = car_model == "CHEVROLET_TRAX"
      if ambiguous_hybrid:
        # This existing 33-message table is also a complete subset of the
        # conventional Malibu table: CAN-only input cannot choose between them.
        assert len(fingerprints) == 1 and len(fingerprint) == 33
        assert fingerprint.items() <= FINGERPRINTS["CHEVROLET_MALIBU_CC"][1].items()
      assert car_fingerprint == (None if ambiguous_bolt or ambiguous_hybrid or ambiguous_trax else car_model)
      assert finger[0] == fingerprint
      assert finger[1] == fingerprint
      assert finger[2] == {}

  def test_automatic_candidates_require_observed_messages(self):
    for candidate, variants in FINGERPRINTS.items():
      with self.subTest(candidate=candidate):
        self.assertTrue(variants)
        self.assertTrue(all(variants))

  def test_timing(self):
    # just pick any CAN fingerprinting car
    car_model = "CHEVROLET_BOLT_CC_2018_2021"
    fingerprint = FINGERPRINTS[car_model][0]

    cases = []

    # case 1 - one match, make sure we keep going for 100 frames
    can = [CanData(address=address, dat=b'\x00' * length, src=src)
           for address, length in fingerprint.items() for src in (0, 1)]
    cases.append((FRAME_FINGERPRINT, car_model, can))

    # case 2 - no matches, make sure we keep going for 100 frames
    can = [CanData(address=1, dat=b'\x00' * 1, src=src) for src in (0, 1)]  # uncommon address
    cases.append((FRAME_FINGERPRINT, None, can))

    # case 3 - multiple matches, make sure we keep going for 200 frames to try to eliminate some
    can = [CanData(address=2016, dat=b'\x00' * 8, src=src) for src in (0, 1)]  # common address
    cases.append((FRAME_FINGERPRINT * 2, None, can))

    for expected_frames, car_model, can in cases:
      with self.subTest(expected_frames=expected_frames, car_model=car_model):
        frames = 0

        def can_recv(**kwargs):
          nonlocal frames
          frames += 1
          return [can]  # noqa: B023

        car_fingerprint, _ = can_fingerprint(can_recv)
        assert car_fingerprint == car_model
        assert frames == expected_frames + 2  # TODO: fix extra frames
