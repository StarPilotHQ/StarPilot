import hashlib
import json
import unittest

from opendbc.car.gm.tests import test_bolt_auto_fingerprint_pure as fingerprint_fixture


EXPECTED = {
  'gm.CHEVROLET_BOLT_CC_2017': '84a0177d0dfb1e3710a4e7b8abdb8fbbc4407c5596c1a14f10071c387279c03d',
  'gm.CHEVROLET_EQUINOX_CC': 'c9f20c9e63f76d1270d315775088e68b6b11f0472f18283164e887fe328cd0f7',
  'gm.CHEVROLET_SUBURBAN_CC': '7ce113446702764109bdad71e722479017d2d1fbf9bd73d68d540db9404d0e37',
  'gm.CADILLAC_CT6_CC': 'b88f1c68422e1f230c22f859614d66ccb789a27c4e33ce2bfd5651481fb27184',
  'gm.CHEVROLET_TRAILBLAZER_CC': '3e69cd3f84de888c97fc9bf24591c01066de2d2ea3bd872dede9be5866e902ac',
  'gm.CHEVROLET_MALIBU_CC': '652c37bd532de3fddd97bd062f349f70f960f8155912ef2a8f99708b3fc1449c',
  'gm.CADILLAC_XT5_CC': 'c642e13066242213ab13638da14cfe497ff43f9178eb44a91b7c95798bc92776',
}


class TestGMAutoIdentity(unittest.TestCase):
  setUp = fingerprint_fixture.TestBoltAutoFingerprint.setUp
  candidates = fingerprint_fixture.TestBoltAutoFingerprint.candidates

  def test_restored_tables_and_joint_discovery(self):
    for identity, digest in EXPECTED.items():
      variants = self.inventory[identity]
      if identity == 'gm.CHEVROLET_MALIBU_CC':
        self.assertEqual(len(variants), 2)
        self.assertEqual(hashlib.sha256(json.dumps(variants[1:], sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                         '1cfbba5e0c98945d6bbc6a9d40df14b26e9782bc256889438c91b71d54a65c9b')
      original = variants[:1] if identity == 'gm.CHEVROLET_MALIBU_CC' else variants
      self.assertEqual(hashlib.sha256(json.dumps(original, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), digest)
      for index, variant in enumerate(variants):
        with self.subTest(identity=identity, variant=index):
          self.assertEqual(self.candidates(variant.items()), [identity])

  def test_existing_unique_identities_do_not_gain_collisions(self):
    from opendbc.car.gm.tests.test_bolt_auto_fingerprint_pure import production_eliminator
    from types import SimpleNamespace
    baseline = {key: variants for key, variants in self.inventory.items() if key not in EXPECTED}
    eliminate = production_eliminator(baseline)
    for identity, variants in baseline.items():
      for index, variant in enumerate(variants):
        remaining = list(baseline)
        for address, length in variant.items():
          remaining = eliminate(SimpleNamespace(address=address, dat=bytes(length)), remaining)
        if remaining == [identity]:
          with self.subTest(identity=identity, variant=index):
            expected = (['gm.CHEVROLET_MALIBU_HYBRID_CC', 'gm.CHEVROLET_MALIBU_CC']
                        if identity == 'gm.CHEVROLET_MALIBU_HYBRID_CC' else [identity])
            self.assertEqual(self.candidates(variant.items()), expected)

  def test_pedal_frame_preserves_original_supported_tables(self):
    identity = 'gm.CHEVROLET_BOLT_CC_2017'
    for index, variant in enumerate(self.inventory[identity]):
      with self.subTest(variant=index):
        self.assertEqual(variant.get(0x201, 6), 6)
        self.assertEqual(self.candidates([*variant.items(), (0x201, 6)]), [identity])

  def test_discovered_gateway_identity_has_healthy_whole_parser(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, gen_empty_fingerprint
    from opendbc.car.gm.fingerprints import FINGERPRINTS
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import (CAR, DBC, GMFlags, ORDINARY_CC_WORD, MALIBU_CC_F1_WORD,
                                     MALIBU_CC_F1_STOCK_WORD, is_ordinary_cc_profile, control_flags)
    from opendbc.car import structs
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
    for identity in EXPECTED:
      if identity == 'gm.CHEVROLET_BOLT_CC_2017':
        continue
      car = getattr(CAR, identity.removeprefix('gm.'))
      for variant in FINGERPRINTS[car]:
        discovered = self.candidates(variant.items())
        self.assertEqual(discovered, [identity])
        fp = gen_empty_fingerprint()
        fp[0].update(variant)
        f1 = car == CAR.CHEVROLET_MALIBU_CC and 0xBE not in variant
        expected_flags = int(GMFlags.CC_LONG | (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0))
        expected_word = MALIBU_CC_F1_WORD if f1 else ORDINARY_CC_WORD
        for alpha, release in ((False, False), (True, False), (True, True)):
          cp = CarInterface.get_params(car, fp, [], alpha, release, False)
          self.assertEqual(cp.carFingerprint, car)
          self.assertTrue(is_ordinary_cc_profile(cp), identity)
          self.assertTrue(cp.openpilotLongitudinalControl)
          self.assertFalse(cp.pcmCruise)
          self.assertFalse(cp.alphaLongitudinalAvailable)
          self.assertFalse(cp.dashcamOnly)
          self.assertEqual(control_flags(cp), expected_flags)
          self.assertEqual(cp.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.gm)
          self.assertEqual(cp.safetyConfigs[0].safetyParam, expected_word)
          disabled = cp.as_reader().as_builder()
          preferences = VehicleStartupPreferences(disable_bolt_long=True)
          preferences.prepare(disabled, fingerprints=fp)
          preferences.finalize(disabled)
          self.assertTrue(is_ordinary_cc_profile(disabled), identity)
          self.assertFalse(disabled.openpilotLongitudinalControl)
          self.assertEqual(disabled.pcmCruise, f1)
          self.assertEqual(disabled.safetyConfigs[0].safetyParam, MALIBU_CC_F1_STOCK_WORD if f1 else expected_word)
          ci = CarInterface(cp)
          packer = CANPacker(DBC[car][Bus.pt])
          for tick in range(40):
            frames = pt_frames(packer, counter=tick % 4)
            accelerator_address = packer.make_can_msg('ECMAcceleratorPos', 0, {})[0]
            if accelerator_address not in variant:
              frames = [frame for frame in frames if frame[0] != accelerator_address]
              if 0xF1 in variant:
                frames.append(packer.make_can_msg('EBCMBrakePedalPosition', 0, {}))
            if cp.flags & GMFlags.HAS_BSM:
              bsm = packer.make_can_msg('BCMBlindSpotMonitor', 0, {})
              observed_length = variant[bsm[0]]
              frames.append((bsm[0], bsm[1][:observed_length], bsm[2]))
            out = ci.update([(1_000_000_000 + tick * 10_000_000, frames)])
          self.assertTrue(out.canValid, identity)
          self.assertFalse(out.canTimeout, identity)

  def test_discovered_2017_bolt_parser_with_actual_pedal_and_camera_configuration(self):
    from opendbc.car import Bus, gen_empty_fingerprint
    from opendbc.car.gm.fingerprints import FINGERPRINTS
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.tests import test_bolt_pedal as pedal_fixture
    from opendbc.car.gm.values import CAR, GMFlags
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

    car = CAR.CHEVROLET_BOLT_CC_2017
    fixture = pedal_fixture.TestBoltPedalStartupParser()
    for variant_index, variant in enumerate(FINGERPRINTS[car]):
      self.assertEqual(self.candidates(variant.items()), ['gm.' + car.name])
      present = 0x201 in variant
      for saved in (False, True):
        for alpha in (False, True):
          for camera in ((True,) if present else (False, True)):
            for disabled in (False, True):
              with self.subTest(variant=variant_index, pedal=saved, alpha=alpha, camera=camera, disabled=disabled):
                fp = gen_empty_fingerprint()
                fp[0].update(variant)
                if camera:
                  fp[2].update({0x180: 4, 0x320: 8})
                cp = CarInterface.get_params(car, fp, [], alpha, False, False)
                VehicleStartupPreferences(disable_bolt_long=disabled).prepare(cp, fingerprints=fp)
                self.assertEqual(bool(cp.flags & GMFlags.PEDAL_LONG), present)
                self.assertTrue(cp.flags & GMFlags.HAS_BSM)
                ci, state = fixture.stream(cp, blindspot=True, pedal_present=present, camera_present=camera)
                self.assertTrue(state.canValid, (variant_index, saved, alpha, camera, disabled, cp.to_dict()))
                self.assertFalse(state.canTimeout)
                self.assertEqual(ci.CS.pedal_sensor_healthy, present)
                self.assertNotIn('ASCMActiveCruiseControlStatus', ci.can_parsers[Bus.cam].vl)
                _, missing_bsm = fixture.stream(cp, missing=(0, 'BCMBlindSpotMonitor'), blindspot=True,
                                               pedal_present=present, camera_present=camera)
                self.assertFalse(missing_bsm.canValid)

  def test_sparse_hybrid_fingerprint_cannot_select_an_automatic_identity(self):
    from types import SimpleNamespace
    from opendbc.car.car_helpers import can_fingerprint
    identity = 'gm.CHEVROLET_MALIBU_HYBRID_CC'
    variants = self.inventory[identity]
    self.assertEqual(len(variants), 1)
    messages = [SimpleNamespace(address=address, dat=bytes(length), src=0)
                for address, length in variants[0].items()]
    self.assertEqual(self.candidates(variants[0].items()),
                     [identity, 'gm.CHEVROLET_MALIBU_CC'])
    selected, observed = can_fingerprint(lambda **_kwargs: [messages])
    self.assertIsNone(selected)
    self.assertEqual(observed[0], variants[0])

  def test_ambiguous_and_manual_identities_remain_unadvertised(self):
    for name in ('CHEVROLET_BOLT_ACC_2022_2023', 'CHEVROLET_BOLT_ACC_2022_2023_PEDAL',
                 'CHEVROLET_BOLT_CC_2022_2023'):
      self.assertNotIn('gm.' + name, self.inventory)

  def test_registered_trax_still_requires_identity_beyond_can(self):
    variants = self.inventory['gm.CHEVROLET_TRAX']
    self.assertEqual(len(variants), 1)
    self.assertEqual(set(self.candidates(variants[0].items())),
                     {'gm.CHEVROLET_TRAX', 'gm.CHEVROLET_EQUINOX', 'gm.CHEVROLET_SILVERADO'})


if __name__ == '__main__':
  unittest.main()
