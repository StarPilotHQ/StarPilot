"""Supported Subaru identities preserve exact matching and stock-only admission."""
import unittest

from opendbc.car.structs import CarParams
from opendbc.car.fw_versions import match_fw_to_car_exact
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.values import CAR

RESTORED = [
  ('SUBARU_ASCENT_2023', 'abs', 1968, 'a520250301'),
  ('SUBARU_ASCENT_2023', 'eps', 1862, '55c0d010'),
  ('SUBARU_ASCENT_2023', 'fwdCamera', 1927, '1721080141122108003b'),
  ('SUBARU_ASCENT_2023', 'fwdCamera', 1927, '17210801410000000000'),
  ('SUBARU_ASCENT_2023', 'engine', 1954, '112ca03007'),
  ('SUBARU_ASCENT_2023', 'transmission', 1955, '05fee70000'),
  ('SUBARU_CROSSTREK_2025', 'abs', 1968, 'a220241505'),
  ('SUBARU_CROSSTREK_2025', 'abs', 1968, 'a220241706'),
  ('SUBARU_CROSSTREK_2025', 'fwdCamera', 1927, '1d21080046142108003d'),
  ('SUBARU_CROSSTREK_2025', 'fwdCamera', 1927, '1b21080044112108013b'),
  ('SUBARU_CROSSTREK_2025', 'engine', 1954, '0422635007'),
  ('SUBARU_CROSSTREK_2025', 'engine', 1954, 'e821637007'),
  ('SUBARU_IMPREZA_2025', 'abs', 1968, 'a220240705'),
  ('SUBARU_IMPREZA_2025', 'eps', 1862, 'c220240101'),
  ('SUBARU_IMPREZA_2025', 'fwdCamera', 1927, '1d210800460000000000'),
  ('SUBARU_IMPREZA_2025', 'engine', 1954, '0422657007'),
  ('SUBARU_LEGACY_2025', 'abs', 1968, 'a120241100'),
  ('SUBARU_LEGACY_2025', 'eps', 1862, '5bc0d11000'),
  ('SUBARU_LEGACY_2025', 'fwdCamera', 1927, '1a210800430e21080138'),
  ('SUBARU_LEGACY_2025', 'engine', 1954, '082ca07007'),
  ('SUBARU_LEGACY_2025', 'transmission', 1955, 'eb17552172'),
]


class TestSupportedSubaruFirmware(unittest.TestCase):
  def test_each_restored_identity_reaches_the_real_exact_matcher(self):
    for name, ecu_name, address, encoded in RESTORED:
      with self.subTest(platform=name, ecu=ecu_name, firmware=encoded):
        platform = CAR[name]
        self.assertIn(platform, FW_VERSIONS)
        versions = FW_VERSIONS[platform]
        live = {(addr, sub): {values[0]} for (_, addr, sub), values in versions.items()}
        live[address, None] = {bytes.fromhex(encoded)}
        self.assertEqual(match_fw_to_car_exact(live, match_brand='subaru', log=False), {platform})

  def test_unknown_engine_and_camera_still_reject_the_platform(self):
    for name in sorted({row[0] for row in RESTORED}):
      platform = CAR[name]
      for (ecu, address, sub), _ in FW_VERSIONS[platform].items():
        if ecu not in (CarParams.Ecu.engine, CarParams.Ecu.fwdCamera):
          continue
        with self.subTest(platform=name, ecu=ecu):
          live = {(addr, sa): {values[0]} for (_, addr, sa), values in FW_VERSIONS[platform].items()}
          live[address, sub] = {b'unknown firmware'}
          self.assertNotIn(platform, match_fw_to_car_exact(live, match_brand='subaru', log=False))

  def test_existing_stock_admission_and_disabled_alpha_are_preserved(self):
    words = {CAR.SUBARU_ASCENT_2023: 177, CAR.SUBARU_CROSSTREK_2025: 17, CAR.SUBARU_LEGACY_2025: 145, CAR.SUBARU_IMPREZA_2025: 8209}
    for platform, word in words.items():
      for alpha in (False, True):
        for release in (False, True):
          with self.subTest(platform=platform, alpha=alpha, release=release):
            cp = CarInterface.get_params(platform, {0: {}, 1: {}, 2: {}}, [], alpha, release, False)
            self.assertFalse(cp.dashcamOnly)
            self.assertFalse(cp.alphaLongitudinalAvailable)
            self.assertFalse(cp.openpilotLongitudinalControl)
            self.assertTrue(cp.pcmCruise)
            self.assertEqual(cp.safetyConfigs[0].safetyParam, word)

  def test_three_byte_diagnostic_response_is_not_a_camera_identity(self):
    for platform in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_LEGACY_2025):
      for versions in FW_VERSIONS[platform].values():
        self.assertNotIn(bytes.fromhex('20020e'), versions)
