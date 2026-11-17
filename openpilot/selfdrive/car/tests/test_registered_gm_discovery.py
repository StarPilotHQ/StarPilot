"""Root-only actual discovery/factory; transport responses are deterministic."""
from contextlib import ExitStack
from dataclasses import replace
from unittest import mock

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car import car_helpers, fw_versions
from opendbc.car import vin as vin_module
from opendbc.car.can_definitions import CanData
from opendbc.car.gm.values import NO_ACC_BOLT_CAR, PEDAL_BOLT_CAR, GMFlags, is_bolt_pedal_profile
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_selection import VehicleSelectionOwner, startup_candidate
from opendbc.car.gm.values import CAR
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.structs import CarParams
from openpilot.common.prefix import OpenpilotPrefix

VIN = '1G1RC6S50HU000001'


def detected(candidate):
  return candidate, gen_empty_fingerprint(), VIN, [], CarParams.FingerprintSource.can, True


@pytest.mark.parametrize('candidate', [CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019,
                                          CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_BOLT_EUV,
                                          CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                                          CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023,
                                          CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, CAR.CHEVROLET_TRAX])
def test_explicit_registered_bus0_gm_keeps_live_vin_fw_can_and_actual_factory(candidate):
  callbacks = []
  queries = []
  layout = gen_empty_fingerprint()
  with OpenpilotPrefix(), ExitStack() as stack:
    stack.enter_context(mock.patch.dict(car_helpers.os.environ, {'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': '', 'FINGERPRINT': ''}))
    vin = stack.enter_context(mock.patch.object(car_helpers, 'get_vin', return_value=(0x648, 0, VIN)))
    present = stack.enter_context(mock.patch.object(car_helpers, 'get_present_ecus', return_value=set()))
    ordered = stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]))
    can = stack.enter_context(mock.patch.object(car_helpers, 'can_fingerprint', return_value=(candidate, layout)))
    factory = car_helpers.interfaces[candidate]
    params = stack.enter_context(mock.patch.object(factory, 'get_params', wraps=factory.get_params))
    query_class = stack.enter_context(mock.patch.object(fw_versions, 'IsoTpParallelQuery'))
    def response(*args, **kwargs):
      assert args[2] == 0
      query_addrs = args[3]
      queries.append({'bus': args[2], 'addresses': tuple(query_addrs), 'request': tuple(args[4])})
      query = mock.Mock()
      query.get_data.return_value = {query_addrs[0]: b'gm-version-observed'}
      return query
    query_class.side_effect = response
    selected = VehicleSelectionOwner(Params(), lambda: True)
    written = selected.choose(selected.snapshot().raw, str(candidate))
    assert written.committed and written.verified
    actual_selected = startup_candidate(Params())
    assert actual_selected == candidate
    ci = car_helpers.get_car(mock.Mock(return_value=[]), mock.Mock(), callbacks.append,
                             False, False, forced_candidate=actual_selected)
  vin.assert_called_once()
  assert vin.call_args.args[2] == (0,)
  assert callbacks and set(callbacks) == {False}
  present.assert_not_called()
  ordered.assert_not_called()
  can.assert_called_once()
  params.assert_called_once()
  assert params.call_args.args[0] == candidate
  assert params.call_args.args[1] is layout
  assert ci.CP.carFingerprint == candidate
  assert ci.CP.carVin == VIN
  assert ci.CP.fingerprintSource == CarParams.FingerprintSource.fixed
  assert len(ci.CP.carFw) > 0 and len(queries) > 0
  assert all(fw.brand == 'gm' and fw.bus == 0 and fw.logging for fw in ci.CP.carFw)
  assert len(params.call_args.args[2]) == len(ci.CP.carFw)
  assert all(fw.fwVersion == b'gm-version-observed' for fw in ci.CP.carFw)


@pytest.mark.parametrize('candidate', [None, HYUNDAI.HYUNDAI_IONIQ_6])
def test_auto_and_other_registered_brand_keep_original_discovery_contract(candidate):
  with OpenpilotPrefix(), mock.patch.object(car_helpers, 'fingerprint', return_value=detected(HYUNDAI.HYUNDAI_IONIQ_6)) as discover:
    ci = car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=candidate)
  assert discover.call_args.kwargs == {}
  assert ci.CP.carFingerprint == HYUNDAI.HYUNDAI_IONIQ_6


def test_invalid_explicit_selection_refuses_before_any_discovery_or_callback():
  callback = mock.Mock()
  with mock.patch.object(car_helpers, 'fingerprint') as discover:
    with pytest.raises(ValueError, match='not registered'):
      car_helpers.get_car(mock.Mock(), mock.Mock(), callback, False, False, forced_candidate='UNREGISTERED')
  discover.assert_not_called()
  callback.assert_not_called()


@pytest.mark.parametrize('topology', ['bus1', 'no_requests'])
def test_changed_gm_request_topology_is_never_guessed_as_normal_bus0(topology):
  original = fw_versions.FW_QUERY_CONFIGS['gm']
  requests = [replace(original.requests[0], bus=1)] if topology == 'bus1' else []
  changed = replace(original, requests=requests, extra_ecus=[])
  with OpenpilotPrefix(), mock.patch.dict(fw_versions.FW_QUERY_CONFIGS, {'gm': changed}), \
       mock.patch.object(car_helpers, 'fingerprint', return_value=detected(CAR.CHEVROLET_VOLT_ASCM)) as discover:
    ci = car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=CAR.CHEVROLET_VOLT_ASCM)
  assert discover.call_args.kwargs == {}
  assert ci.CP.carFingerprint == CAR.CHEVROLET_VOLT_ASCM


def test_direct_scoped_request_rechecks_topology_before_any_io():
  original = fw_versions.FW_QUERY_CONFIGS['gm']
  changed = replace(original, requests=[replace(original.requests[0], bus=1)])
  callback = mock.Mock()
  with mock.patch.dict(fw_versions.FW_QUERY_CONFIGS, {'gm': changed}), \
       mock.patch.object(car_helpers, 'get_vin') as vin:
    with pytest.raises(ValueError, match='topology'):
      car_helpers.fingerprint(mock.Mock(), mock.Mock(), callback, None, known_gm_bus0=True)
  callback.assert_not_called()
  vin.assert_not_called()


def test_wrong_selected_cache_is_not_reused_as_gm_discovery_proof():
  cached = CarParams(carFingerprint=HYUNDAI.HYUNDAI_IONIQ_6, brand='hyundai', carVin=VIN)
  with OpenpilotPrefix(), mock.patch.object(car_helpers, 'fingerprint', return_value=detected(CAR.CHEVROLET_VOLT_ASCM)) as discover:
    car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False,
                        forced_candidate=CAR.CHEVROLET_VOLT_ASCM, cached_params=cached)
  assert discover.call_args.args[3] is None
  assert discover.call_args.kwargs == {'known_gm_bus0': True}


def test_gm_fw_collection_records_all_matching_responses_not_a_fake_match():
  # Current GM query metadata explicitly collects firmware for logging. Merely
  # receiving it must not become an exact firmware identity assertion.
  firmware = CarParams.CarFw(brand='gm', bus=0, logging=True, address=0x24b, fwVersion=b'observed')
  assert fw_versions.build_fw_dict([firmware]) == {}


def test_matching_selected_cache_keeps_existing_verification_and_no_live_fw_read():
  cached = CarParams(carFingerprint=CAR.CHEVROLET_VOLT_ASCM, brand='gm', carVin=VIN,
                     carFw=[CarParams.CarFw(brand='gm', bus=0, logging=True, address=0x24b, fwVersion=b'observed')])
  callbacks = []
  with OpenpilotPrefix(), ExitStack() as stack:
    stack.enter_context(mock.patch.dict(car_helpers.os.environ, {'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': '', 'FINGERPRINT': ''}))
    vin = stack.enter_context(mock.patch.object(car_helpers, 'get_vin'))
    firmware = stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions'))
    stack.enter_context(mock.patch.object(car_helpers, 'can_fingerprint', return_value=(CAR.CHEVROLET_VOLT_ASCM, gen_empty_fingerprint())))
    ci = car_helpers.get_car(mock.Mock(return_value=[]), mock.Mock(), callbacks.append, False, False,
                             forced_candidate=CAR.CHEVROLET_VOLT_ASCM, cached_params=cached)
  vin.assert_not_called()
  firmware.assert_not_called()
  assert callbacks == [False]
  assert ci.CP.carVin == VIN and ci.CP.carFw[0].fwVersion == b'observed'


@pytest.mark.parametrize('skip,disable_cache,developer', [('', '', ''), ('1', '', ''), ('', '1', ''), ('', '', 'HYUNDAI_IONIQ_6')])
def test_explicit_selection_precedence_and_discovery_environment_are_preserved(skip, disable_cache, developer):
  observed = []
  def capture(*args, **kwargs):
    observed.append(kwargs)
    return detected(HYUNDAI.HYUNDAI_IONIQ_6)
  with OpenpilotPrefix(), mock.patch.dict(car_helpers.os.environ, {'SKIP_FW_QUERY': skip, 'DISABLE_FW_CACHE': disable_cache, 'FINGERPRINT': developer}), \
       mock.patch.object(car_helpers, 'fingerprint', side_effect=capture):
    owner = VehicleSelectionOwner(Params(), lambda: True)
    assert owner.choose(owner.snapshot().raw, str(CAR.CHEVROLET_VOLT_ASCM)).verified
    selected = startup_candidate(Params(), developer_fingerprint=bool(developer))
    ci = car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=selected)
  assert observed == ([{}] if developer else [{'known_gm_bus0': True}])
  assert ci.CP.carFingerprint == (HYUNDAI.HYUNDAI_IONIQ_6 if developer else CAR.CHEVROLET_VOLT_ASCM)


@pytest.mark.parametrize('skip', ['', '1'])
@pytest.mark.parametrize('disable_cache', ['', '1'])
def test_actual_scoped_fingerprint_respects_existing_skip_and_cache_flags(skip, disable_cache):
  cached = CarParams(carFingerprint=CAR.CHEVROLET_VOLT_ASCM, brand='gm', carVin=VIN,
                     carFw=[CarParams.CarFw(brand='gm', bus=0, logging=True, address=0x24b, fwVersion=b'cached')])
  live_fw = [CarParams.CarFw(brand='gm', bus=0, logging=True, address=0x24b, fwVersion=b'live')]
  callback = mock.Mock()
  receive = mock.Mock(return_value=[])
  layout = gen_empty_fingerprint()
  with mock.patch.dict(car_helpers.os.environ, {'SKIP_FW_QUERY': skip, 'DISABLE_FW_CACHE': disable_cache, 'FINGERPRINT': ''}), ExitStack() as stack:
    vin = stack.enter_context(mock.patch.object(car_helpers, 'get_vin', return_value=(0x648, 0, VIN)))
    firmware = stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions', return_value=live_fw))
    present = stack.enter_context(mock.patch.object(car_helpers, 'get_present_ecus', return_value=set()))
    ordered = stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]))
    can = stack.enter_context(mock.patch.object(car_helpers, 'can_fingerprint', return_value=(CAR.CHEVROLET_VOLT_ASCM, layout)))
    result = car_helpers.fingerprint(receive, mock.Mock(), callback, cached, known_gm_bus0=True)
  can.assert_called_once_with(receive, vin=result[2], camera_cruise_messages=set())
  receive.assert_called_once_with()
  present.assert_not_called()
  ordered.assert_not_called()
  assert result[0] == CAR.CHEVROLET_VOLT_ASCM and result[1] is layout
  assert callback.call_args_list and all(call.args == (False,) for call in callback.call_args_list)
  if skip:
    vin.assert_not_called()
    firmware.assert_not_called()
    assert result[2] == vin_module.VIN_UNKNOWN and result[3] == []
  elif disable_cache:
    vin.assert_called_once()
    assert vin.call_args.args[2] == (0,)
    firmware.assert_called_once()
    assert firmware.call_args.kwargs == {'query_brand': 'gm'}
    assert result[2] == VIN and result[3] == live_fw
  else:
    vin.assert_not_called()
    firmware.assert_not_called()
    assert result[2] == VIN and result[3][0].fwVersion == b'cached'


def test_scoped_query_is_per_call_never_carried_into_auto_or_other_make():
  scopes = []
  def discovery(*args, **kwargs):
    scopes.append(kwargs)
    return detected(HYUNDAI.HYUNDAI_IONIQ_6)
  with OpenpilotPrefix(), mock.patch.object(car_helpers, 'fingerprint', side_effect=discovery):
    for selected in (CAR.CHEVROLET_VOLT_ASCM, None, HYUNDAI.HYUNDAI_IONIQ_6):
      ci = car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=selected)
      assert ci.CP.carFingerprint == (selected or HYUNDAI.HYUNDAI_IONIQ_6)
  assert scopes == [{'known_gm_bus0': True}, {}, {}]


@pytest.mark.parametrize('forced,developer,expected,scoped', [
  (None, str(CAR.CHEVROLET_VOLT_ASCM), CAR.CHEVROLET_VOLT_ASCM, True),
  (None, str(HYUNDAI.HYUNDAI_IONIQ_6), HYUNDAI.HYUNDAI_IONIQ_6, False),
  (CAR.CHEVROLET_VOLT_ASCM, str(HYUNDAI.HYUNDAI_IONIQ_6), CAR.CHEVROLET_VOLT_ASCM, True),
  (HYUNDAI.HYUNDAI_IONIQ_6, str(CAR.CHEVROLET_VOLT_ASCM), HYUNDAI.HYUNDAI_IONIQ_6, False),
])
def test_registered_environment_selection_and_forced_precedence(forced, developer, expected, scoped):
  # Recorded Volts use fixed source with empty saved selection. Card bypasses
  # saved selection when FINGERPRINT is present; this is a distinct entry path.
  with OpenpilotPrefix(), mock.patch.dict(car_helpers.os.environ, {'FINGERPRINT': developer}), \
       mock.patch.object(car_helpers, 'fingerprint', return_value=detected(developer)) as discovery:
    ci = car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=forced)
  assert ci.CP.carFingerprint == expected
  assert discovery.call_args.kwargs == ({'known_gm_bus0': True} if scoped else {})


@pytest.mark.parametrize('fingerprint,brand', [(HYUNDAI.HYUNDAI_IONIQ_6, 'hyundai'),
                                            (CAR.CHEVROLET_VOLT, 'gm'),
                                            (CAR.CHEVROLET_VOLT_ASCM, 'hyundai')])
def test_environment_gm_never_reuses_other_configuration_cache(fingerprint, brand):
  cached = CarParams(carFingerprint=fingerprint, brand=brand, carVin=VIN)
  with OpenpilotPrefix(), mock.patch.dict(car_helpers.os.environ, {'FINGERPRINT': str(CAR.CHEVROLET_VOLT_ASCM)}), \
       mock.patch.object(car_helpers, 'fingerprint', return_value=detected(CAR.CHEVROLET_VOLT_ASCM)) as discovery:
    car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, cached_params=cached)
  assert discovery.call_args.args[3] is None
  assert discovery.call_args.kwargs == {'known_gm_bus0': True}


@pytest.mark.parametrize('topology', ['bus1', 'empty'])
def test_effective_query_registry_is_checked_in_addition_to_config(topology):
  original = fw_versions.REQUESTS
  effective = [(brand, config, replace(request, bus=1) if brand == 'gm' else request)
               for brand, config, request in original] if topology == 'bus1' else [r for r in original if r[0] != 'gm']
  with mock.patch.object(fw_versions, 'REQUESTS', effective), \
       mock.patch.object(car_helpers, 'get_vin') as vin:
    assert not car_helpers._gm_fw_uses_normal_bus0()
    with pytest.raises(ValueError, match='topology'):
      car_helpers.fingerprint(mock.Mock(), mock.Mock(), mock.Mock(), None, known_gm_bus0=True)
  vin.assert_not_called()


def test_actual_environment_factory_queries_normal_bus0_and_keeps_selection():
  peer = RecordedIsoTpEcu()
  callbacks = []
  layout = gen_empty_fingerprint()
  with OpenpilotPrefix(), mock.patch.dict(car_helpers.os.environ, {
      'FINGERPRINT': str(CAR.CHEVROLET_VOLT_ASCM), 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       mock.patch.object(car_helpers, 'get_present_ecus') as global_ecus, \
       mock.patch.object(car_helpers, 'get_fw_versions_ordered') as global_fw, \
       mock.patch.object(car_helpers, 'can_fingerprint', return_value=(None, layout)):
    selected = startup_candidate(Params(), developer_fingerprint=True)
    assert selected is None
    ci = car_helpers.get_car(peer.recv, peer.send, callbacks.append, False, False, forced_candidate=selected)
  assert ci.CP.carFingerprint == CAR.CHEVROLET_VOLT_ASCM
  assert ci.CP.carVin == VIN and ci.CP.fingerprintSource == CarParams.FingerprintSource.fixed
  assert ci.CP.carFw and all(fw.brand == 'gm' and fw.bus == 0 and fw.logging for fw in ci.CP.carFw)
  assert callbacks and set(callbacks) == {False}
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)
  global_ecus.assert_not_called()
  global_fw.assert_not_called()


@pytest.mark.parametrize('candidate', sorted(PEDAL_BOLT_CAR))
@pytest.mark.parametrize('pedal_bus', [None, 0, 1, 2])
@pytest.mark.parametrize('qualified_removed', [False, True])
def test_discovery_choice_preserves_actual_factory_topology_and_authority(candidate, pedal_bus, qualified_removed):
  layout = gen_empty_fingerprint()
  if pedal_bus is not None:
    layout[pedal_bus][0x201] = 6
  if qualified_removed:
    layout[0].update({0x184: 8, 0x34A: 5, 0x348: 5, 0xC9: 8, 0x1C4: 8, 0x1E1: 7,
                      0x1F5: 8, 0xBD: 7, 0x232: 8, 0x3D1: 8, 0xBE: 6})
  def discovered(*args, **kwargs):
    assert kwargs == {'known_gm_bus0': True}
    return candidate, layout, VIN, [], CarParams.FingerprintSource.can, True
  with OpenpilotPrefix(), mock.patch.object(car_helpers, 'fingerprint', side_effect=discovered):
    ci = car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=candidate)
    direct = car_helpers.interfaces[candidate].get_params(candidate, layout, [], False, False, docs=False)
  assert bool(ci.CP.flags & GMFlags.PEDAL_LONG) == (pedal_bus == 0)
  assert is_bolt_pedal_profile(ci.CP) == (pedal_bus == 0 and qualified_removed)
  assert ci.CP.carFingerprint == candidate
  assert ci.CP.openpilotLongitudinalControl == direct.openpilotLongitudinalControl
  assert ci.CP.pcmCruise == direct.pcmCruise and ci.CP.dashcamOnly == direct.dashcamOnly
  assert [(item.safetyModel, item.safetyParam) for item in ci.CP.safetyConfigs] == [(item.safetyModel, item.safetyParam) for item in direct.safetyConfigs]
  assert ci.CP.flags == direct.flags and ci.CP.alternativeExperience == direct.alternativeExperience
  assert ci.CP.lateralTuning.to_dict() == direct.lateralTuning.to_dict()
  assert ci.CP.longitudinalTuning.to_dict() == direct.longitudinalTuning.to_dict()
  if pedal_bus == 0 and not qualified_removed:
    assert ci.CP.dashcamOnly and not ci.CP.openpilotLongitudinalControl
    assert ci.CP.safetyConfigs[0].safetyModel == CarParams.SafetyModel.noOutput
  if candidate in NO_ACC_BOLT_CAR:
    assert not ci.CP.pcmCruise


class RecordedIsoTpEcu:
  """Actual query/UDS framing, inert CAN peer. No real bus or firmware claim."""
  def __init__(self, response_bus=0):
    self.frames = []
    self.pending = {}
    self.sent = []
    self.response_bus = response_bus

  def send(self, messages):
    self.sent.extend(messages)
    for message in messages:
      payload = message.dat
      if payload[0] >> 4 == 3:
        self.frames.extend(self.pending.pop(message.address, []))
        continue
      if payload[0] >> 4 != 0:
        continue
      request = payload[1:1 + payload[0]]
      if message.address in (0x7df, 0x7e0) and request == b'\x22\xf1\x90':
        self.reply(0x7e0, 0x7e8, b'\x62\xf1\x90' + VIN.encode())
      elif request == b'\x3e' and message.address < 0x700:
        self.reply(message.address, message.address + 0x400, b'\x7e')
      elif request[:1] == b'\x1a' and message.address < 0x700:
        self.reply(message.address, message.address + 0x400, b'\x5a' + request[1:2] + b'FW')

  def reply(self, tx, rx, value):
    if len(value) < 8:
      self.frames.append(CanData(rx, bytes([len(value)]) + value + bytes(7 - len(value)), self.response_bus))
    else:
      self.frames.append(CanData(rx, bytes([0x10 | len(value) >> 8, len(value) & 0xff]) + value[:6], self.response_bus))
      self.pending[tx] = [CanData(rx, bytes([0x20 | (i + 1) % 16]) + value[start:start + 7] + bytes(max(0, 7 - len(value[start:start + 7]))), self.response_bus)
                          for i, start in enumerate(range(6, len(value), 7))]

  def recv(self, wait_for_one=False):
    values, self.frames = self.frames, []
    return [values] if values else []


def test_actual_vin_and_firmware_query_serializes_only_bus0_with_live_uds_replies():
  peer = RecordedIsoTpEcu()
  observed = []
  with mock.patch.object(vin_module, 'IsoTpParallelQuery', wraps=vin_module.IsoTpParallelQuery) as vin_query, \
       mock.patch.object(fw_versions, 'IsoTpParallelQuery', wraps=fw_versions.IsoTpParallelQuery) as fw_query:
    result = vin_module.get_vin(peer.recv, peer.send, (0,), timeout=0.01)
    firmware = fw_versions.get_fw_versions(peer.recv, peer.send, observed.append, query_brand='gm', timeout=0.01)
  assert result == (0x7e8, 0, VIN)
  assert firmware and all(item.brand == 'gm' and item.bus == 0 and item.logging and item.fwVersion == b'FW' for item in firmware)
  assert all(message.src == 0 for message in peer.sent)
  assert all(call.args[2] == 0 for call in vin_query.call_args_list + fw_query.call_args_list)
  assert observed == []  # Actual GM get_fw_versions toggles only bus%4==1.
  assert fw_versions.build_fw_dict(firmware) == {}


def test_actual_vin_query_never_accepts_reply_from_other_bus():
  peer = RecordedIsoTpEcu(response_bus=1)
  assert vin_module.get_vin(peer.recv, peer.send, (0,), timeout=0.001, retry=1) == (-1, -1, vin_module.VIN_UNKNOWN)
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)


def test_selected_gm_factory_receives_actual_live_vin_and_uds_firmware():
  peer = RecordedIsoTpEcu()
  normal = []
  layout = gen_empty_fingerprint()
  live_fw = fw_versions.get_fw_versions
  def vin_query(receive, send, buses):
    return vin_module.get_vin(receive, send, buses, timeout=0.01)
  def firmware_query(receive, send, callback, *, query_brand):
    return live_fw(receive, send, callback, query_brand=query_brand, timeout=0.01)
  def before_factory(cp, candidate, actual_layout, actual_fw):
    assert candidate == CAR.CHEVROLET_VOLT_ASCM and actual_layout is layout
    assert cp.carVin == VIN and actual_fw and all(item.fwVersion == b'FW' for item in actual_fw)
    return cp
  hook = mock.Mock(side_effect=before_factory)
  with OpenpilotPrefix(), ExitStack() as stack:
    stack.enter_context(mock.patch.dict(car_helpers.os.environ, {'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': '', 'FINGERPRINT': ''}))
    stack.enter_context(mock.patch.object(car_helpers, 'get_vin', side_effect=vin_query))
    stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions', side_effect=firmware_query))
    stack.enter_context(mock.patch.object(car_helpers, 'can_fingerprint', return_value=(CAR.CHEVROLET_VOLT_ASCM, layout)))
    present = stack.enter_context(mock.patch.object(car_helpers, 'get_present_ecus', return_value=set()))
    ordered = stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]))
    owner = VehicleSelectionOwner(Params(), lambda: True)
    assert owner.choose(owner.snapshot().raw, str(CAR.CHEVROLET_VOLT_ASCM)).verified
    ci = car_helpers.get_car(peer.recv, peer.send, normal.append, False, False,
                             pre_create_hook=hook, forced_candidate=startup_candidate(Params()))
  hook.assert_called_once()
  present.assert_not_called()
  ordered.assert_not_called()
  assert ci.CP.carFingerprint == CAR.CHEVROLET_VOLT_ASCM and ci.CP.carVin == VIN
  assert ci.CP.carFw and all(item.brand == 'gm' and item.logging and item.bus == 0 for item in ci.CP.carFw)
  assert peer.sent and all(message.src == 0 for message in peer.sent)
  assert normal and set(normal) == {False}



@pytest.mark.parametrize('elapsed, expected_wait', [(0.0, 0.1), (0.04, 0.06), (0.15, None)])
def test_actual_scoped_fresh_query_preserves_subscriber_warmup_before_vin(elapsed, expected_wait):
  clock = [10.0]
  order = []

  def obd(value):
    assert value is False
    order.append('obd')
    clock[0] += elapsed

  def sleep(duration):
    assert duration == pytest.approx(expected_wait)
    clock[0] += duration
    order.append('warmup')

  def vin(*args):
    assert clock[0] - 10.0 >= 0.1 - 1e-12
    order.append('vin')
    return 0x648, 0, VIN

  with mock.patch.dict(car_helpers.os.environ, {'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': '', 'FINGERPRINT': ''}), \
       mock.patch.object(car_helpers.time, 'monotonic', side_effect=lambda: clock[0]), \
       mock.patch.object(car_helpers.time, 'sleep', side_effect=sleep) as wait, \
       mock.patch.object(car_helpers, 'get_vin', side_effect=vin), \
       mock.patch.object(car_helpers, 'get_fw_versions', return_value=[]), \
       mock.patch.object(car_helpers, 'match_fw_to_car', return_value=(False, set())), \
       mock.patch.object(car_helpers, 'can_fingerprint', return_value=(CAR.CHEVROLET_VOLT_ASCM, gen_empty_fingerprint())):
    car_helpers.fingerprint(mock.Mock(return_value=[]), mock.Mock(), obd, None, known_gm_bus0=True)
  assert order == (['obd', 'vin', 'obd'] if expected_wait is None else ['obd', 'warmup', 'vin', 'obd'])
  assert wait.call_count == (expected_wait is not None)
