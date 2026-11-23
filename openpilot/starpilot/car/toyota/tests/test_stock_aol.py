"""Dom bare stock-cruise coverage with exact independent native physical sources."""
from itertools import product
from contextlib import ExitStack

import pytest
from opendbc.car import CanData, gen_empty_fingerprint, structs
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota import stock_aol
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.aol.tests.test_toyota_secoc import parsed_state
from openpilot.starpilot.aol.tests.test_toyota_secoc_joined import run_joined
from openpilot.starpilot.car.toyota.aol import qualified


@pytest.mark.parametrize('vehicle,hybrid,bsm,alpha', product(stock_aol.PROFILES, (False, True), (False, True), (False, True)))
def test_stock_factory_keeps_pcm_longitudinal(vehicle, hybrid, bsm, alpha):
  fp = gen_empty_fingerprint()
  if bsm:
    fp[0][0x3F6] = 8
  fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
  cp = CarInterface.get_params(vehicle, fp, fw, alpha, False, False)
  assert cp.safetyConfigs[0].safetyParam == stock_aol.PROFILES[vehicle]
  assert not cp.openpilotLongitudinalControl and not cp.autoResumeSng
  assert stock_aol.configured(cp) and qualified(cp)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)


@pytest.mark.parametrize('vehicle', stock_aol.PROFILES)
@pytest.mark.parametrize('ordinary', (False, True))
def test_stock_callers_parser_native_controls_and_card(vehicle, ordinary):
  run_joined(vehicle, ordinary=ordinary)


@pytest.mark.parametrize('vehicle', stock_aol.PROFILES)
@pytest.mark.parametrize('field,value', (('openpilotLongitudinalControl', True), ('passive', True), ('dashcamOnly', True),
                                        ('notCar', True), ('secOcRequired', True), ('alternativeExperience', 160)))
def test_stock_configuration_refuses_authority_substitution(vehicle, field, value):
  cp = CarInterface.get_params(vehicle, gen_empty_fingerprint(), [], False, False, False)
  setattr(cp, field, value)
  assert not qualified(cp)


@pytest.mark.parametrize('vehicle', stock_aol.PROFILES)
def test_stock_hardware_is_not_a_fallback_for_an_adapter(vehicle):
  for bus, address in product((0, 1, 2), (0x201, 0x2FF, 0x2AA)):
    fp = gen_empty_fingerprint()
    fp[bus][address] = 1
    cp = CarInterface.get_params(vehicle, fp, [], False, False, False)
    assert not stock_aol.configured(cp)
    assert cp.safetyConfigs[0].safetyParam != stock_aol.PROFILES[vehicle]


@pytest.mark.parametrize('mirror', (False, True))
def test_camry_stock_radar_and_startup_mirror(mirror):
  from opendbc.car.toyota.values import CAR

  fp = gen_empty_fingerprint()
  fp[0].update({0xAA: 8, 0x262: 8, 0x343: 8})
  fp[1].update({0x2FF: 8, **dict.fromkeys(range(0x680, 0x686), 8)})
  if mirror:
    fp[2].update(fp[0])
  for alpha in (False, True):
    cp = CarInterface.get_params(CAR.TOYOTA_CAMRY, fp, [], alpha, False, False)
    assert cp.safetyConfigs[0].safetyParam == 0x6249
    assert not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
    assert stock_aol.configured(cp) and qualified(cp)


@pytest.mark.parametrize('change', ('pedal', 'filter', 'status', 'unknown_radar', 'radar_dlc',
                                    'camera_only_acc', 'independent_camera', 'camera_4cb', 'acc_dlc'))
def test_camry_stock_mirror_rejects_adapter_or_ambiguous_topology(change):
  from opendbc.car.toyota.values import CAR

  fp = gen_empty_fingerprint()
  fp[0].update({0xAA: 8, 0x262: 8, 0x343: 8})
  fp[1].update({0x2FF: 8, **dict.fromkeys(range(0x680, 0x686), 8)})
  fp[2].update(fp[0])
  if change == 'pedal':
    fp[0][0x201] = 6
  elif change == 'filter':
    fp[0][0x2AA] = 8
  elif change == 'status':
    fp[0][0x2FF] = 8
  elif change == 'unknown_radar':
    del fp[1][0x685]
  elif change == 'radar_dlc':
    fp[1][0x2FF] = 4
  elif change == 'camera_only_acc':
    del fp[0][0x343]
  elif change == 'independent_camera':
    fp[2][0x123] = 8
  elif change == 'camera_4cb':
    fp[0][0x4CB] = fp[2][0x4CB] = 8
  else:
    fp[0][0x343] = fp[2][0x343] = 7
  assert not stock_aol.hardware_supported(CAR.TOYOTA_CAMRY, fp)


@pytest.mark.parametrize('word', sorted(stock_aol.WORDS))
def test_native_stock_profile_rejects_faults_and_never_owns_acceleration(word):
  vehicle = next(car for car, profile in stock_aol.PROFILES.items() if profile == word)
  cp = CarInterface.get_params(vehicle, gen_empty_fingerprint(), [], False, False, False)
  native = libsafety_py.libsafety
  with ExitStack() as cleanup:
    cleanup.callback(native.set_alternative_experience, 0)
    cleanup.callback(native.set_safety_hooks, structs.CarParams.SafetyModel.noOutput, 0)
    def reset():
      native.init_tests()
      native.set_alternative_experience(32)
      native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word)
      native.set_timer(1_000_000)
      native.set_aol_test_heartbeat(True)
      _, frames = parsed_state(CarInterface(cp))
      for address, payload, bus in frames:
        assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, payload))
      native.aol_set_host_request(3)
      assert native.aol_get_request_mask() == native.aol_get_permission_mask() == 1
      return frames
    frames = reset()
    for address in (0x183, 0x343):
      assert native.safety_fwd_hook(2, address) == 0
    assert not native.safety_tx_hook(libsafety_py.make_CANPacket(0x343, 0, b'\0\1'+bytes(6)))
    main = 0x365 if word in stock_aol.ALT_MAIN_WORDS else 0x1D3
    for source in (main, 0x262):
      frames = reset()
      original = next(payload for address, payload, bus in frames if address == source and bus == 0)
      assert len(original) == ((5 if word in stock_aol.EPS5_WORDS else 8) if source == 0x262 else (7 if source == 0x365 else 8))
      native.safety_rx_hook(libsafety_py.make_CANPacket(source, 0, original[:-1]))
      assert native.aol_get_permission_mask() == 0
      reset()
      native.set_timer(1_500_001)
      native.set_aol_test_heartbeat(True)
      for address, payload, bus in frames:
        if address != source:
          native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, payload))
      native.aol_set_host_request(1)
      assert native.aol_get_permission_mask() == 0


def test_all_toyota_and_lexus_factory_identities_have_an_explicit_aol_path():
  from opendbc.car.toyota.values import CAR
  for vehicle in CAR:
    cp = CarInterface.get_params(vehicle, gen_empty_fingerprint(), [], False, False, False)
    cp.secOcKeyAvailable = cp.secOcRequired
    assert qualified(cp), vehicle


@pytest.mark.parametrize('length', (4, 8))
def test_matrix_hybrid_firmware_is_refused_without_a_factory_assertion(length):
  from opendbc.car.toyota.values import CAR
  fp = gen_empty_fingerprint()
  fp[0].update({0x201: 6, 0x2FF: length})
  cp = CarInterface.get_params(CAR.TOYOTA_MATRIX_RETROFIT, fp,
    [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)], False, False, False)
  assert cp.dashcamOnly and not cp.openpilotLongitudinalControl and not qualified(cp)


def test_dom_sienna_engine_variant_is_retained_for_identification():
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  from opendbc.car.toyota.values import CAR
  from opendbc.car.fw_versions import match_fw_to_car
  rows = [structs.CarParams.CarFw(ecu=ecu, address=addr, subAddress=sub or 0, fwVersion=versions[0],
                                 brand='toyota', bus=0)
          for (ecu, addr, sub), versions in FW_VERSIONS[CAR.TOYOTA_SIENNA_4TH_GEN].items()]
  engine = next(row for row in rows if row.ecu == structs.CarParams.Ecu.engine)
  engine.fwVersion = b'\x01896630869000'+bytes(4)
  exact, matches = match_fw_to_car(rows, '', log=False)
  assert exact and CAR.TOYOTA_SIENNA_4TH_GEN in matches


@pytest.mark.parametrize('word', sorted(stock_aol.WORDS))
@pytest.mark.parametrize('source', ('main', 'eps'))
def test_host_stock_physical_sources_withdraw_at_native_deadline(word, source):
  vehicle = next(car for car, profile in stock_aol.PROFILES.items() if profile == word)
  cp = CarInterface.get_params(vehicle, gen_empty_fingerprint(), [], False, False, False)
  interface = CarInterface(cp)
  state, frames = parsed_state(interface)
  assert state.cruiseState.available and not state.steerFaultTemporary
  main = 0x365 if word in stock_aol.ALT_MAIN_WORDS else 0x1D3
  selected = main if source == 'main' else 0x262
  origin = 1_070_000_000
  deadline = 303_031_000 if source == 'main' else 400_000_000
  state = interface.update([(origin + deadline + 1, [frame for frame in frames if frame[0] != selected])])
  assert not state.cruiseState.available if source == 'main' else state.steerFaultTemporary
  raw = next(frame for frame in frames if frame[0] == selected)
  state = interface.update([(origin + deadline + 2, [raw])])
  assert state.cruiseState.available if source == 'main' else not state.steerFaultTemporary
  address, payload, bus = raw
  state = interface.update([(origin + deadline + 3, [CanData(address, payload[:-1], bus)])])
  assert not state.cruiseState.available if source == 'main' else state.steerFaultTemporary
