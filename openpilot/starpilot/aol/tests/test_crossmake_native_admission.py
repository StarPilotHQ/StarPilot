"""All registered makes share exact-packet structural reuse and live vetoes."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from openpilot.cereal import messaging
from openpilot.starpilot.aol import runtime, transport_pause, vehicle
from openpilot.starpilot.aol.tests.test_native_observation_admission import receive
from openpilot.starpilot.aol.tests.test_panda_inventory import NOW, bound_state, sm_for
from openpilot.starpilot.aol.tests.test_runtime import car_state

BRANDS = ('honda', 'hyundai', 'gm', 'ford', 'mazda', 'tesla', 'toyota', 'subaru', 'nissan', 'chrysler', 'rivian', 'volkswagen', 'volvo')


def profile(brand):
  if brand in ('honda', 'hyundai', 'gm', 'ford', 'mazda', 'tesla', 'toyota'):
    from openpilot.starpilot.aol.tests.test_vehicle_policy_cache import profile as factory
    cp = factory(brand)
  elif brand == 'chrysler':
    from opendbc.car.chrysler.aol_contract import CHRYSLER_AOL_CARS
    from openpilot.starpilot.aol.tests.test_chrysler import cp_for
    cp = cp_for(sorted(CHRYSLER_AOL_CARS)[0])
  elif brand == 'volkswagen':
    from opendbc.car.volkswagen.values import CAR
    from openpilot.starpilot.aol.tests.test_mqb_topology_contract import params
    cp = params(CAR.VOLKSWAGEN_GOLF_MK7, False, False, 4, False)
  else:
    fp = gen_empty_fingerprint()
    if brand == 'subaru':
      from opendbc.car.subaru.values import CAR
      identity = CAR.SUBARU_OUTBACK_2023
    elif brand == 'nissan':
      from opendbc.car.nissan.values import CAR
      identity = CAR.NISSAN_LEAF
    elif brand == 'volvo':
      from opendbc.car.volvo.values import CAR
      identity = CAR.VOLVO_V60
    else:
      from opendbc.car.rivian.values import CAR
      identity = CAR.RIVIAN_R1
      fp[0][0x321] = 7
    cp = interfaces[identity].get_params(identity, fp, [], False, False, False)
  policy = vehicle.policy_for(cp)
  assert policy.intent_supported, brand
  config = (vehicle.hyundai.aol_safety_config(cp) if brand == 'hyundai' else
            vehicle.ford.aol_safety_config(cp) if brand == 'ford' else
            vehicle.volkswagen.aol_safety_config(cp) if brand == 'volkswagen' else cp.safetyConfigs[-1])
  assert config is not None
  config.safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert vehicle.native_matches_cp(cp, int(config.safetyModel.raw), int(config.safetyParam)), brand
  return cp


@pytest.fixture(params=BRANDS)
def cp(request):
  return profile(request.param)


def full_oracle(sm, cp, *, now=NOW, session=None):
  actual = runtime.current_native(sm, cp, now_ns=now, axis_session_id=session)
  with patch.object(runtime, 'native_admission_key', return_value=None):
    expected = runtime.current_native(sm, cp, now_ns=now, axis_session_id=session)
  assert actual == expected
  return actual


def test_matrix_covers_every_registered_make():
  assert set(BRANDS) == set(vehicle._PORTS)


def test_one_validation_per_original_packet_and_owner_isolation(cp):
  before = cp.to_dict()
  state = bound_state(cp)
  sm = sm_for(cp, state)
  other = sm_for(cp, state)
  with patch.object(runtime, 'native_matches_cp', wraps=runtime.native_matches_cp) as check:
    for tick in range(10):
      assert runtime.current_native(sm, cp, now_ns=NOW + tick * 1_000_000) == state
    assert check.call_count == 1
    assert runtime.current_native(other, cp, now_ns=NOW) == state
    assert check.call_count == 2
    updated = bound_state(cp, now=NOW + 10_000_000)
    receive(sm, updated)
    assert runtime.current_native(sm, cp, now_ns=NOW + 10_000_000) == updated
    assert check.call_count == 3
  assert cp.to_dict() == before


@pytest.mark.parametrize('field', ('brand', 'carFingerprint', 'flags', 'passive', 'notCar', 'dashcamOnly',
                                  'pcmCruise', 'openpilotLongitudinalControl', 'alternativeExperience',
                                  'safetyParam', 'safetyModel', 'safetyConfigs', 'carVin'))
def test_hot_configuration_mutations_match_full_validation(cp, field):
  state = bound_state(cp)
  sm = sm_for(cp, state)
  assert runtime.current_native(sm, cp, now_ns=NOW) == state
  key = vehicle.native_admission_key(cp)
  if field == 'safetyParam':
    cp.safetyConfigs[-1].safetyParam ^= 1
  elif field == 'safetyModel':
    cp.safetyConfigs[-1].safetyModel = 'noOutput'
  elif field == 'safetyConfigs':
    cp.safetyConfigs = []
  elif field in ('passive', 'notCar', 'dashcamOnly', 'pcmCruise', 'openpilotLongitudinalControl'):
    setattr(cp, field, not getattr(cp, field))
  else:
    setattr(cp, field, {'brand': 'unregistered', 'carFingerprint': 'unregistered', 'flags': int(cp.flags) ^ 1,
                        'alternativeExperience': int(cp.alternativeExperience) ^ 1, 'carVin': 'MUTATED'}[field])
  assert vehicle.native_admission_key(cp) != key
  with patch.object(runtime, 'native_matches_cp', wraps=runtime.native_matches_cp) as check:
    full_oracle(sm, cp)
    assert check.call_count == 2


@pytest.mark.parametrize('change', ('rx', 'heartbeat', 'fault', 'inventory', 'serial', 'lateral', 'longitudinal',
                                   'session', 'expired', 'future', 'invalid', 'not_alive', 'not_seen'))
def test_every_live_change_matches_full_validation(cp, change):
  state = bound_state(cp)
  sm = sm_for(cp, state)
  assert runtime.current_native(sm, cp, now_ns=NOW) == state
  now, session = NOW, None
  if change in ('rx', 'heartbeat', 'fault', 'inventory'):
    field, value = {'rx': ('safetyRxChecksInvalid', True), 'heartbeat': ('heartbeatLost', True),
                    'fault': ('faults', 1), 'inventory': ('safetyParam', state.pandaInventory[-1].safetyParam ^ 1)}[change]
    slots = (*state.pandaInventory[:-1], replace(state.pandaInventory[-1], **{field: value}))
    receive(sm, replace(state, pandaInventory=slots))
  elif change in ('serial', 'lateral', 'longitudinal'):
    field, value = {'serial': ('pandaSerial', 'other'), 'lateral': ('lateralAllowed', False),
                    'longitudinal': ('longitudinalAllowed', False)}[change]
    receive(sm, replace(state, **{field: value}))
  elif change == 'session':
    session = 'other'
  elif change == 'expired':
    now = state.validUntilMonoTime + 1
  elif change == 'future':
    now -= 1
  else:
    getattr(sm, {'invalid': 'valid', 'not_alive': 'alive', 'not_seen': 'seen'}[change])['aolSafetyWire'] = False
  result = full_oracle(sm, cp, now=now, session=session)
  if change in ('lateral', 'longitudinal'):
    assert result is not None and not getattr(result, 'lateralAllowed' if change == 'lateral' else 'longitudinalAllowed')
  else:
    assert result is None


def test_latest_panda_veto_is_checked_after_structural_hit(cp):
  state = bound_state(cp)
  sm = sm_for(cp, state)
  assert transport_pause.healthy_transport(sm, cp, car_state(), state.axisSessionId, NOW)
  message = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True, logMonoTime=state.sourcePandaStatesMonoTime)
  for index, panda in enumerate(sm['pandaStates']):
    message.pandaStates[index] = panda
  message.pandaStates[-1].heartbeatLost = True
  sm.update_msgs(NOW / 1e9, [message.as_reader()])
  assert runtime.current_native(sm, cp, now_ns=NOW) == state
  assert not transport_pause.healthy_transport(sm, cp, car_state(), state.axisSessionId, NOW)


def test_same_builder_mutation_during_miss_cannot_bind_old_key(cp):
  state = bound_state(cp)
  sm = sm_for(cp, state)
  original = runtime.native_inventory_matches_cp

  def mutate(native, params):
    result = original(native, params)
    params.carVin = 'CHANGED-DURING-MISS'
    return result

  with patch.object(runtime, 'native_inventory_matches_cp', side_effect=mutate):
    assert runtime.current_native(sm, cp, now_ns=NOW) == state
  assert sm._aol_native_admission is None
  full_oracle(sm, cp)


def test_oversize_cp_bypasses_reuse_without_changing_admission(cp):
  cp.carVin = 'X' * (vehicle._POLICY_CACHE_MAX_BYTES + 1)
  state = bound_state(cp)
  sm = sm_for(cp, state)
  assert vehicle.native_admission_key(cp) is None
  with patch.object(runtime, 'native_matches_cp', wraps=runtime.native_matches_cp) as check:
    assert runtime.current_native(sm, cp, now_ns=NOW) == state
    assert runtime.current_native(sm, cp, now_ns=NOW) == state
    assert check.call_count == 2


def test_snapshot_failure_falls_back_to_full_validation(cp):
  state = bound_state(cp)
  sm = sm_for(cp, state)
  assert runtime.current_native(sm, cp, now_ns=NOW) == state
  with patch.object(vehicle, '_car_params_bytes', return_value=None), \
       patch.object(runtime, 'native_matches_cp', wraps=runtime.native_matches_cp) as check:
    assert runtime.current_native(sm, cp, now_ns=NOW) == state
    assert runtime.current_native(sm, cp, now_ns=NOW) == state
    assert check.call_count == 2
  assert sm._aol_native_admission is None


@pytest.mark.parametrize('legacy', (False, True))
def test_legacy_or_slotted_owner_keeps_full_validation(cp, legacy):
  state = bound_state(cp)
  if legacy:
    state = replace(state, sourcePandaStatesMonoTime=0, pandaInventory=())
  original = sm_for(cp, state)

  class SlottedOwner:
    __slots__ = ('valid', 'alive', 'seen', 'logMonoTime')

    def __init__(self):
      for name in self.__slots__:
        setattr(self, name, getattr(original, name))

    def __getitem__(self, name):
      return original[name]

  sm = original if legacy else SlottedOwner()
  expected = state if not legacy or len(cp.safetyConfigs) == 1 else None
  with patch.object(runtime, 'native_matches_cp', wraps=runtime.native_matches_cp) as check:
    assert runtime.current_native(sm, cp, now_ns=NOW) == expected
    assert runtime.current_native(sm, cp, now_ns=NOW) == expected
    assert check.call_count == 2


def test_native_admission_visits_only_the_current_vehicle_owner(cp):
  from contextlib import ExitStack
  config = (vehicle.hyundai.aol_safety_config(cp) if cp.brand == 'hyundai' else
            vehicle.ford.aol_safety_config(cp) if cp.brand == 'ford' else
            vehicle.volkswagen.aol_safety_config(cp) if cp.brand == 'volkswagen' else cp.safetyConfigs[-1])
  model, param = int(config.safetyModel.raw), int(config.safetyParam)
  assert vehicle.native_matches_cp(cp, model, param)
  with ExitStack() as cleanup:
    for brand, port in vehicle._PORTS.items():
      if brand != cp.brand:
        cleanup.enter_context(patch.object(port, 'native_accepts_cp', side_effect=AssertionError('unrelated owner called')))
    assert vehicle.native_matches_cp(cp, model, param)
    assert not vehicle.native_matches_cp(cp, model, param ^ 0x40000000)


def test_registered_dispatch_matches_original_all_ports_oracle(cp):
  # Evaluate the original function unchanged, including its owner special cases.
  original = vehicle.native_matches_cp
  config = (vehicle.hyundai.aol_safety_config(cp) if cp.brand == 'hyundai' else
            vehicle.ford.aol_safety_config(cp) if cp.brand == 'ford' else
            vehicle.volkswagen.aol_safety_config(cp) if cp.brand == 'volkswagen' else cp.safetyConfigs[-1])
  model, param = int(config.safetyModel.raw), int(config.safetyParam)
  for bad_model, bad_param in ((model, param), (model, param ^ 0x40000000), (0, param), (model, -1)):
    expected = bool(any(port.native_accepts_cp(cp, bad_model, bad_param) for port in vehicle._PORTS.values()) and
                    bad_model == int(cp.safetyConfigs[0].safetyModel.raw) and
                    bad_param == int(cp.safetyConfigs[-1].safetyParam))
    # Multi-owner/family-special paths retain their original production checks.
    if cp.brand in ('hyundai', 'ford', 'volkswagen') or cp.brand == 'honda' and len(cp.safetyConfigs) == 2:
      assert original(cp, bad_model, bad_param) == (bad_model == model and bad_param == param)
    else:
      assert original(cp, bad_model, bad_param) == expected


def test_no_host_event_skips_all_family_temporary_qualifications(cp):
  from contextlib import ExitStack
  from types import SimpleNamespace
  from opendbc.car.structs import CarState
  from opendbc.car.hyundai import canfd_angle_aol, canfd_stock_aol, classic_scc_aol
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  feedback = TransportPauseFeedback(cp)
  state = CarState(canValid=True)
  with ExitStack() as cleanup:
    for module in (canfd_angle_aol, canfd_stock_aol, classic_scc_aol):
      cleanup.enter_context(patch.object(module, 'qualified', side_effect=AssertionError('healthy cycle qualified unrelated temporary role')))
    assert not feedback._temporary_host_intent_current({'onroadEvents': []}, cp, state, SimpleNamespace(), 10, 10,
                                                       latched=True, producer='producer')
