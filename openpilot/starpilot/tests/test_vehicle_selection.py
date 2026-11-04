import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.values import CAR
from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_selection import (KEY, VehicleSelectionOwner, choices, read_selection, startup_candidate)


class TestVehicleSelection(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = Params(self.directory.name)
    self.parked = True
    self.owner = VehicleSelectionOwner(self.params, lambda: self.parked)

  def test_absent_auto_and_registered_choices(self):
    self.assertEqual(self.owner.snapshot().platform, None)
    self.assertTrue(self.owner.snapshot().valid)
    self.assertIsNone(startup_candidate(self.params))
    self.assertIn(CAR.KIA_XCEED_PHEV, [choice.platform for choice in choices()])

  def test_elantra_refresh_label_preserves_saved_identity(self):
    selected = next(choice for choice in choices() if choice.platform == CAR.HYUNDAI_ELANTRA_HEV_2024)
    self.assertEqual(selected.label, "Hyundai Elantra Hybrid 2024-2026")
    self.assertTrue(self.owner.choose(None, selected.platform).verified)
    self.assertEqual(startup_candidate(self.params), CAR.HYUNDAI_ELANTRA_HEV_2024)

  def test_multiple_documented_trims_share_neutral_platform_choice(self):
    from opendbc.car.hyundai.values import CAR as HYUNDAI
    from opendbc.car.values import PLATFORMS

    self.assertGreater(len(PLATFORMS[HYUNDAI.HYUNDAI_IONIQ_6].config.car_docs), 1)
    selected = [choice for choice in choices() if choice.platform == HYUNDAI.HYUNDAI_IONIQ_6]
    self.assertEqual(len(selected), 1)
    self.assertEqual(selected[0].label, 'Hyundai Ioniq 6')

  def test_volt_harness_choices_are_under_chevrolet(self):
    from opendbc.car.gm.values import CAR as GM

    expected = {
      GM.CHEVROLET_VOLT: 'Chevrolet Volt (OBD-II Harness) 2017-18',
      GM.CHEVROLET_VOLT_ASCM: 'Chevrolet Volt (ASCM Harness) 2017-18',
      GM.CHEVROLET_VOLT_2019: 'Chevrolet Volt (SDGM Harness) 2019',
      GM.CHEVROLET_VOLT_CAMERA: 'Chevrolet Volt (Camera Harness)',
      GM.CHEVROLET_VOLT_CC: 'Chevrolet Volt (CC)',
    }
    catalog = {choice.platform: choice for choice in self.owner.choices()}
    for platform, label in expected.items():
      with self.subTest(platform=platform):
        self.assertEqual(catalog[platform].make, 'Chevrolet')
        self.assertEqual(catalog[platform].label, label)
        original = self.owner.snapshot()
        self.assertTrue(self.owner.choose(original.raw, str(platform)).verified)
        self.assertEqual(startup_candidate(self.params), platform)

  def test_k4_2026_documentation_keeps_one_manual_platform_and_factory(self):
    from opendbc.car.hyundai.interface import CarInterface
    from opendbc.car.values import PLATFORMS

    labels = {doc.name for doc in PLATFORMS[CAR.KIA_K4_2025].config.car_docs}
    self.assertEqual(labels, {'Kia K4 (without HDA II) 2025', 'Kia K4 (without HDA II) 2026',
                              'Kia K4 (with HDA II) 2025'})
    selected = [choice for choice in self.owner.choices() if choice.platform == CAR.KIA_K4_2025]
    self.assertEqual(len(selected), 1)
    self.assertTrue(self.owner.choose(None, selected[0].platform).committed)
    self.assertEqual(startup_candidate(self.params), CAR.KIA_K4_2025)
    candidate = startup_candidate(self.params)
    assert candidate is not None
    cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)
    self.assertEqual(cp.carFingerprint, CAR.KIA_K4_2025)
    self.assertFalse(cp.openpilotLongitudinalControl)

  def test_strict_document_and_corrupt_source_preserved_until_explicit_repair(self):
    for bad in (b'{}', b'{"version":true,"platform":null}', b'{"version":1,"platform":"FAKE"}',
                b'{"version":1,"platform":null,"platform":"KIA_XCEED_PHEV"}', b'\xff'):
      with self.subTest(raw=bad):
        Path(self.params.get_param_path(KEY)).write_bytes(bad)
        snapshot = read_selection(self.params)
        self.assertTrue(snapshot.readable)
        self.assertFalse(snapshot.valid)
        self.assertEqual(snapshot.raw, bad)
        self.assertEqual(startup_candidate(self.params), 'MOCK')
        self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), bad)
    self.assertIsNone(startup_candidate(self.params, developer_fingerprint=True))

  def test_parked_exact_source_selection_and_auto_restoration(self):
    original = self.owner.snapshot()
    saved = self.owner.choose(original.raw, str(CAR.KIA_XCEED_PHEV))
    self.assertTrue(saved.committed and saved.verified, saved)
    selected = self.owner.snapshot()
    self.assertEqual(selected.platform, CAR.KIA_XCEED_PHEV)
    self.assertEqual(startup_candidate(self.params), CAR.KIA_XCEED_PHEV)
    self.assertEqual(json.loads(Path(self.params.get_param_path(KEY)).read_bytes()),
                     {'version': 1, 'platform': CAR.KIA_XCEED_PHEV})
    self.assertFalse(self.owner.choose(original.raw, None).committed)
    self.parked = False
    self.assertFalse(self.owner.choose(selected.raw, None).committed)
    self.assertEqual(self.owner.snapshot(), selected)
    self.parked = True
    reset = self.owner.choose(selected.raw, None)
    self.assertTrue(reset.committed and reset.verified)
    self.assertIsNone(self.owner.snapshot().platform)
    self.assertIsNone(startup_candidate(self.params))

  def test_authority_revocation_while_staging_prevents_commit(self):
    from openpilot.starpilot import saved_document
    original_sync = saved_document.os.fsync

    def revoke(fd):
      self.parked = False
      return original_sync(fd)

    with patch.object(saved_document.os, 'fsync', side_effect=revoke):
      result = self.owner.choose(None, str(CAR.KIA_XCEED_PHEV))
    self.assertFalse(result.committed)
    self.assertIsNone(self.owner.snapshot().raw)

  def test_unreadable_source_does_not_revert_to_auto_or_get_rewritten(self):
    path = Path(self.params.get_param_path(KEY))
    outside = path.parent / 'other-vehicle-choice'
    raw = b'{"version":1,"platform":"KIA_CEED"}'
    outside.write_bytes(raw)
    path.symlink_to(outside)
    observed = self.owner.snapshot()
    self.assertFalse(observed.readable)
    self.assertEqual(startup_candidate(self.params), 'MOCK')
    self.assertFalse(self.owner.choose(observed.raw, None).committed)
    self.assertEqual(outside.read_bytes(), raw)

  def test_native_candidate_override_changes_identity_source_only(self):
    from opendbc.car import car_helpers

    detected = (CAR.KIA_CEED, gen_empty_fingerprint(), '00000000000000000', [],
                car.CarParams.FingerprintSource.can, True)
    cached = car.CarParams(carFingerprint=CAR.KIA_CEED)
    with patch.object(car_helpers, 'fingerprint', return_value=detected) as identify:
      ci = car_helpers.get_car(lambda wait_for_one=False: [], lambda _: None, lambda _: None,
                               False, False, cached_params=cached, forced_candidate=str(CAR.KIA_XCEED_PHEV))
    self.assertIsNone(identify.call_args.args[-1])
    self.assertEqual(ci.CP.carFingerprint, CAR.KIA_XCEED_PHEV)
    self.assertEqual(ci.CP.fingerprintSource, car.CarParams.FingerprintSource.fixed)
    self.assertFalse(ci.CP.openpilotLongitudinalControl)
    with patch.object(car_helpers, 'fingerprint', return_value=detected) as identify:
      automatic = car_helpers.get_car(lambda wait_for_one=False: [], lambda _: None, lambda _: None,
                                      False, False, cached_params=cached, forced_candidate=None)
    self.assertIs(identify.call_args.args[-1], cached)
    self.assertEqual(automatic.CP.carFingerprint, CAR.KIA_CEED)
    self.assertEqual(automatic.CP.fingerprintSource, car.CarParams.FingerprintSource.can)
    with patch.object(car_helpers, 'fingerprint', return_value=detected), self.assertRaises(ValueError):
      car_helpers.get_car(lambda wait_for_one=False: [], lambda _: None, lambda _: None,
                          False, False, forced_candidate='UNREGISTERED')


if __name__ == '__main__':
  unittest.main()
