"""Dom standstill pulse/copy behavior and physical vetoes."""

from types import SimpleNamespace
import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.sng import StopAndGo, SUPPORTED, prepare, selected
from opendbc.car.subaru.sng_sender import replacement
from opendbc.car.subaru.software_cruise import prepare as prepare_software, selected as software_selected
from opendbc.car.subaru.values import CAR


@pytest.mark.parametrize('candidate', list(CAR))
def test_exact_startup_without_alpha_long(candidate):
  for alpha in (False, True):
    cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha, False, False)
    prior = cp.to_dict()
    prepare(cp, True)
    assert not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
    assert selected(cp) == (candidate in SUPPORTED)
    if candidate not in SUPPORTED:
      assert cp.to_dict() == prior


def state():
  out = structs.CarState(canValid=True, standstill=True, gearShifter='drive')
  out.cruiseState.available = out.cruiseState.enabled = True
  cc = structs.CarControl(enabled=True)
  cc.hudControl.leadVisible = True
  return cc, SimpleNamespace(out=out, close_distance=4.0, sng_lead_ready=True)


def test_one_bounded_departure_burst_and_no_old_request_after_disable():
  owner = StopAndGo()
  cc, cs = state()
  assert owner.update(cc, cs, 0) == (False, False)
  cs.close_distance = 4.019607
  assert owner.update(cc, cs, 1) == (True, False)
  assert sum(owner.update(cc, cs, frame)[0] for frame in range(2, 30)) == 14
  for frame in range(30, 100):
    cs.close_distance = 4.0 if frame % 2 else 4.019607
    assert not owner.update(cc, cs, frame)[0]
  cc.enabled = False
  assert owner.update(cc, cs, 100) == (False, False)
  cc.enabled = True
  assert owner.update(cc, cs, 101) == (False, False)


def test_dom_global_hold_timing_and_clear_on_lead_loss():
  owner = StopAndGo()
  cc, cs = state()
  pulses = [frame for frame in range(111) if owner.update(cc, cs, frame)[1]]
  assert pulses == [51, 52, 53, 54, 55, 106, 107, 108, 109, 110]
  cs.close_distance += 0.02
  assert owner.update(cc, cs, 111)[0]
  cc.hudControl.leadVisible = False
  assert owner.update(cc, cs, 112) == (False, False)
  cc.hudControl.leadVisible = True
  assert owner.update(cc, cs, 113) == (False, False)


@pytest.mark.parametrize(
  'field,value',
  [
    ('brakePressed', True),
    ('gasPressed', True),
    ('gearShifter', 'reverse'),
    ('steerFaultPermanent', True),
    ('steerFaultTemporary', True),
    ('canValid', False),
    ('canTimeout', True),
  ],
)
def test_physical_vetoes_clear_pending_burst(field, value):
  owner = StopAndGo()
  cc, cs = state()
  owner.update(cc, cs, 0)
  cs.close_distance += 0.02
  assert owner.update(cc, cs, 1)[0]
  setattr(cs.out, field, value)
  assert owner.update(cc, cs, 2) == (False, False)
  assert owner.remaining == 0 and owner.previous_distance is None


@pytest.mark.parametrize('address', [0x40, 0x139])
def test_raw_passthrough_keeps_opaque_bits_and_physical_brake(address):
  original = bytearray(bytes.fromhex('00af34ff0568abf9'))
  original[0] = ((address & 255) + (address >> 8) + sum(original[1:])) & 255
  addr, result, bus = replacement(address, original)
  assert addr == address and bus == 2
  assert result[1] & 15 == 0 and result[1] & 0xF0 == original[1] & 0xF0
  assert result[2:] == original[2:]
  assert result[0] == ((address & 255) + (address >> 8) + sum(result[1:])) & 255
  _, changed, _ = replacement(address, original, throttle=True, speed=True)
  if address == 0x139:
    assert changed[4:] == original[4:]
  else:
    assert changed[2:4] == original[2:4] and changed[5:] == original[5:]


def test_software_cruise_and_sng_compose_without_longitudinal_authority():
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  prepare_software(cp, True)
  prepare(cp, True)
  assert cp.safetyConfigs[0].safetyParam == 520 and selected(cp) and software_selected(cp)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable


@pytest.mark.parametrize('key', ['SubaruSNG', 'SubaruRedneckCruise'])
def test_real_parked_settings_row_requires_confirmation_and_fresh_authority(key, tmp_path):
  from dataclasses import replace
  from openpilot.common.params import Params
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.feature_settings_state import row_change
  params = Params(str(tmp_path))
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  permitted = [True]
  owner = FeatureSettingsOwner(params, lambda group: permitted[0], vehicle_fingerprint=lambda: cp.carFingerprint,
                               vehicle_params=lambda: cp, configuration_vehicle=lambda: True)
  row = next(row for row in owner.snapshot('vehicle', parked=True, system_long=False, lateral_context=True, metric=True).rows if row.key == key)
  assert row.available and row.value == 'Off'
  request = row_change(row)
  assert request is not None and not owner.apply(request)
  permitted[0] = False
  assert not owner.apply(replace(request, confirmation=True))
  permitted[0] = True
  assert owner.apply(replace(request, confirmation=True))
  assert params.get_bool(key)
  assert not owner.apply(replace(request, confirmation=True))
  assert cp.safetyConfigs[0].safetyParam == 0 and not cp.autoResumeSng


def test_sng_standstill_request_withdrawal_is_exact_and_does_not_grant_axis():
  from openpilot.starpilot.car.subaru.aol import sng_standstill_lateral_inhibit
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 0
  stopped = structs.CarState(standstill=True)
  assert not sng_standstill_lateral_inhibit(cp, stopped)
  cp.alternativeExperience = 32
  assert sng_standstill_lateral_inhibit(cp, stopped)
  prepare(cp, True)
  assert sng_standstill_lateral_inhibit(cp, stopped)
  stopped.standstill = False
  assert not sng_standstill_lateral_inhibit(cp, stopped)
  # Non-Legacy Subarus keep upstream standstill-only inhibit even with initial_wait=True.
  stopped.brakePressed = True
  assert not sng_standstill_lateral_inhibit(cp, stopped, initial_wait=True)
  # Legacy 2025 rolling with foot lightly on brake waits before Panda's initial !brake_pressed grant, then releases once brake lifts.
  legacy_cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, gen_empty_fingerprint(), [], False, False, False)
  legacy_cp.alternativeExperience = 32
  assert sng_standstill_lateral_inhibit(legacy_cp, stopped, initial_wait=True)
  assert not sng_standstill_lateral_inhibit(legacy_cp, stopped, initial_wait=False)
  stopped.brakePressed = False
  assert not sng_standstill_lateral_inhibit(legacy_cp, stopped, initial_wait=True)
  cp.openpilotLongitudinalControl = True
  stopped.standstill = True
  assert not sng_standstill_lateral_inhibit(cp, stopped)
