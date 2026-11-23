from dataclasses import replace
from pathlib import Path
from opendbc.car import gen_empty_fingerprint
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.values import CAR, FROGGER_ANGLE_SIBLINGS
from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


def test_avh_parked_row_confirmation_and_authority(tmp_path):
  params = Params(str(tmp_path))
  cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, gen_empty_fingerprint(), [], False, False, False)
  permitted = [True]
  owner = FeatureSettingsOwner(params, lambda group: permitted[0], vehicle_fingerprint=lambda: cp.carFingerprint,
                               vehicle_params=lambda: cp, configuration_vehicle=lambda: True)
  row = next(row for row in owner.snapshot('vehicle', parked=True, system_long=False, lateral_context=True, metric=True).rows
             if row.key == 'SubaruAvhStartup')
  assert row.available and row.value == 'Off'
  request = row_change(row)
  assert request is not None and not owner.apply(request)
  permitted[0] = False
  assert not owner.apply(replace(request, confirmation=True))
  permitted[0] = True
  assert owner.apply(replace(request, confirmation=True))
  assert params.get_bool('SubaruAvhStartup')
  assert not owner.apply(replace(request, confirmation=True))
  assert cp.safetyConfigs[0].safetyParam == 145 and not cp.openpilotLongitudinalControl


def test_only_supported_identity_and_exact_saved_boolean_startup(tmp_path):
  from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
  from opendbc.car.subaru.avh import selected
  params = Params(str(tmp_path))
  for raw, safe, expected in ((None, b'0', False), (b'0', b'0', False), (b'1', b'0', True),
                              (b'true', b'0', False), (b'1', b'1', False), (b'1', b'invalid', False)):
    if raw is None:
      params.remove('SubaruAvhStartup')
    else:
      Path(params.get_param_path('SubaruAvhStartup')).write_bytes(raw)
    Path(params.get_param_path('SafeMode')).write_bytes(safe)
    choice = VehicleStartupPreferences.read(params, enabled=True)
    assert choice.subaru_avh_startup == expected
    assert not VehicleStartupPreferences.read(params, enabled=False).subaru_avh_startup
    for car in CAR:
      cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], True, False, False)
      before = cp.to_dict()
      choice.prepare(cp)
      assert selected(cp) == (expected and car in FROGGER_ANGLE_SIBLINGS | {CAR.SUBARU_LEGACY_2025})
      assert not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
      if car not in FROGGER_ANGLE_SIBLINGS | {CAR.SUBARU_LEGACY_2025} or not expected:
        assert cp.to_dict() == before



def test_release_saved_choice_retained_without_avh_marker(tmp_path):
  from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
  from opendbc.car.subaru.avh import selected
  params = Params(str(tmp_path))
  params.put_bool('SubaruAvhStartup', True, block=True)
  for raw in (b'1', b'invalid', b''):
    Path(params.get_param_path('IsReleaseBranch')).write_bytes(raw)
    choice = VehicleStartupPreferences.read(params, enabled=True)
    cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, gen_empty_fingerprint(), [], False, False, False)
    choice.prepare(cp)
    assert not selected(cp) and cp.safetyConfigs[0].safetyParam == 145
    assert Path(params.get_param_path('SubaruAvhStartup')).read_bytes() == b'1'


def test_original_source_clock_and_conflicting_duplicate():
  from opendbc.car.subaru.avh import AvhSources, INPUTS, checksum
  from opendbc.car.subaru.tests.test_avh import sample
  batch_source = AvhSources()
  batch_frames = [(a, sample(a, counter), 1) for counter in (1, 2) for a in INPUTS]
  batch_source.observe([(10_020_000_000, batch_frames)], boot_ns=10_020_000_000, mono_ns=1_020_000_000)
  assert not batch_source.blocked and batch_source.fresh(1.02)
  for target in INPUTS:
    source = AvhSources()
    for counter in (1, 2):
      frames = [(a, sample(a, counter), 1) for a in INPUTS]
      stamp = 10_000_000_000 + counter * 10_000_000
      source.observe([(stamp, frames)], boot_ns=stamp, mono_ns=stamp - 9_000_000_000)
    assert source.fresh(1.02)
    original = dict(source.frames)
    source.observe([(10_100_000_000, frames)], boot_ns=10_100_000_000, mono_ns=1_100_000_000)
    assert source.frames == original and not source.fresh(1.321)
    data = bytearray(source.frames[target][1])
    data[7] ^= 1
    data[0] = checksum(target, data)
    source.observe([(10_110_000_000, [(target, bytes(data), 1)])], boot_ns=10_110_000_000, mono_ns=1_110_000_000)
    assert source.blocked and not source.fresh(1.11)
