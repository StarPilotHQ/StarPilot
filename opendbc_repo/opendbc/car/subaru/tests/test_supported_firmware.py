"""Supported Subaru identities preserve exact matching and stock-only admission."""
import unittest
import pytest

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
  ('SUBARU_IMPREZA_2025', 'fwdCamera', 1927, '1d21080046142108003d'),
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
    words = {CAR.SUBARU_ASCENT_2023: 177, CAR.SUBARU_CROSSTREK_2025: 17, CAR.SUBARU_IMPREZA_2025: 17, CAR.SUBARU_LEGACY_2025: 145}
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


def recorded_impreza_firmware():
  # 388aeb4f2701bd8e/00000008--3be806066e: actual query responses, not a generated catalog sample.
  rows = (
    ('abs', 0x7b0, 'a220240705', True, 1, True),
    ('engine', 0x7a2, '0422657007', True, 1, True),
    ('eps', 0x746, 'c220240101', True, 1, True),
    ('fwdCamera', 0x787, '1d210800460000000000', False, 0, True),
    ('fwdCamera', 0x787, '20020e', True, 0, True),
    ('abs', 0x7b0, 'a220240705', False, 1, False),
    ('engine', 0x7a2, '0422657007', False, 1, False),
  )
  return [CarParams.CarFw(ecu=ecu, address=address, fwVersion=bytes.fromhex(version),
                         brand='subaru', logging=logging, bus=bus, obdMultiplexing=obd)
          for ecu, address, version, logging, bus, obd in rows]


@pytest.mark.parametrize('full_startup', [False, True])
def test_recorded_impreza_firmware_selects_exact_identity_without_overrides(full_startup):
  from opendbc.car.fw_versions import match_fw_to_car, build_fw_dict

  fw = recorded_impreza_startup_firmware() if full_startup else recorded_impreza_firmware()
  exact, matches = match_fw_to_car(fw, '0' * 17, log=False)
  assert exact and matches == {CAR.SUBARU_IMPREZA_2025}
  assert bytes.fromhex('20020e') not in build_fw_dict(fw)[0x787, None]
  for ecu in (CarParams.Ecu.abs, CarParams.Ecu.engine, CarParams.Ecu.fwdCamera):
    unknown = [CarParams.CarFw(ecu=row.ecu, address=row.address, subAddress=row.subAddress,
                              fwVersion=b'unknown firmware' if row.ecu == ecu and not row.logging else row.fwVersion,
                              brand=row.brand, logging=row.logging) for row in fw]
    assert CAR.SUBARU_IMPREZA_2025 not in match_fw_to_car(unknown, '0' * 17, log=False)[1]


def recorded_impreza_startup_firmware(camera_version='1d21080046142108003d'):
  # 388aeb4f2701bd8e/0000001e--432395cebf: preserve all actual responses, including other query brands.
  rows = (
    ('abs', 1968, 'a220240705', 'subaru', True, 1, True),
    ('engine', 1954, '0422657007', 'subaru', True, 1, True),
    ('eps', 1862, 'c220240101', 'subaru', True, 1, True),
    ('fwdCamera', 1927, camera_version, 'subaru', False, 0, True),
    ('fwdCamera', 1927, '20020e', 'subaru', True, 0, True),
    ('abs', 1968, 'a220240705', 'subaru', False, 1, False),
    ('engine', 1954, '0422657007', 'subaru', False, 1, False),
    ('hvac', 1971, 'f100b90100', 'hyundai', True, 1, True),
    ('fwdRadar', 2000, 'f100000002', 'hyundai', False, 1, True),
    ('fwdCamera', 1988, 'f100ac0300', 'hyundai', False, 1, True),
    ('adas', 1840, 'f100bb0f07', 'hyundai', True, 1, False),
  )
  return [CarParams.CarFw(ecu=ecu, address=address, fwVersion=bytes.fromhex(version),
                         brand=brand, logging=logging, bus=bus, obdMultiplexing=obd)
          for ecu, address, version, brand, logging, bus, obd in rows]
