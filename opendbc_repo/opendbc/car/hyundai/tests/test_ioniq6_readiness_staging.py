from types import SimpleNamespace

import pytest

from openpilot.common.params import Params
from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.hyundai.tests import test_ioniq6_handoff as handoff
from opendbc.car.hyundai.tests.test_ioniq6_startup_neutral import prepared_card


@pytest.mark.parametrize('alternate', (False, True))
def test_actual_constructor_persists_readiness_before_ecu_disable(monkeypatch, alternate):
  events = []
  put = Params.put_bool
  exchange = handoff.FakeIoniq6Bus.exchange

  def persist(params, key, value, block=False):
    if key == 'ControlsReady' and value:
      assert block
      assert not params.get('CarParams') and not params.get_bool('FirmwareQueryDone')
    result = put(params, key, value, block=block)
    if key == 'ControlsReady' and value:
      events.append('readiness_persisted')
    return result

  def transaction(bus, request, response):
    if request == b'\x28\x01\x01':
      events.append('disable')
    return exchange(bus, request, response)

  monkeypatch.setattr(Params, 'put_bool', persist)
  monkeypatch.setattr(handoff.FakeIoniq6Bus, 'exchange', transaction)
  case = handoff.TestIoniq6PrepublicationHandoff()
  try:
    case.assert_card_prepublication_fallback({}, alternate=alternate)
  finally:
    case.doCleanups()
  assert 'readiness_persisted' in events, 'Readiness persistence must finish while stock ADAS is still transmitting'
  assert events.index('readiness_persisted') < events.index('disable')


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('fault', (None, 'published_cp', 'firmware_done', 'already_ready', 'stale', 'rx', 'hardware', 'enabled', 'ignition'))
def test_staging_requires_current_diagnostic_only_inventory(monkeypatch, offset, fault):
  owner, _, _, panda = prepared_card(monkeypatch, alternate=True, offset=offset, aol=True)
  values = {'ControlsReady': False, 'IsOffroad': False, 'FirmwareQueryDone': False}
  writes = []

  def put(key, value, block=False):
    assert block
    writes.append((key, value))
    values[key] = value

  owner.params = SimpleNamespace(get_bool=lambda key: bool(values.get(key)),
                                get=lambda key: values.get(key), put_bool=put)
  owner.vehicle_startup = SimpleNamespace(owner=None)
  owner.sm.update = lambda timeout: None
  for index, state in enumerate(owner.sm['pandaStates']):
    state.pandaType = log.PandaState.PandaType.dos if offset and index == 0 else log.PandaState.PandaType.redPanda
    state.safetyModel = structs.CarParams.SafetyModel.elm327
    state.safetyParam = 1
    state.ignitionLine, state.ignitionCan = True, False
    state.controlsAllowed = False
  if fault == 'published_cp':
    values['CarParams'] = b'previous-publication'
  elif fault == 'firmware_done':
    values['FirmwareQueryDone'] = True
  elif fault == 'already_ready':
    values['ControlsReady'] = True
  elif fault == 'stale':
    owner.sm.logMonoTime['pandaStates'] -= 300_000_001
  elif fault == 'rx':
    panda.safetyRxChecksInvalid = True
  elif fault == 'hardware':
    panda.faults = [log.PandaState.FaultType.interruptRateCan1]
  elif fault == 'enabled':
    panda.controlsAllowed = True
  elif fault == 'ignition':
    panda.ignitionLine = False
  stage = getattr(owner, 'stage_ioniq6_startup_readiness', None)
  assert callable(stage), 'Readiness must be persisted before the ECU transaction'
  assert stage(owner.CP) is (fault is None)
  assert writes == ([('ControlsReady', True)] if fault is None else [])


def test_native_fault_during_persistence_revokes_staged_readiness(monkeypatch):
  owner, _, _, panda = prepared_card(monkeypatch, alternate=True, aol=True)
  values = {'ControlsReady': False}
  owner.vehicle_startup = SimpleNamespace(owner=None)
  owner.sm.update = lambda timeout: None
  panda.safetyModel, panda.safetyParam = structs.CarParams.SafetyModel.elm327, 1
  panda.controlsAllowed = False
  panda.ignitionLine, panda.ignitionCan = True, False
  writes = []

  def put(key, value, block=False):
    assert block
    writes.append((key, value))
    values[key] = value
    if value:
      panda.safetyRxChecksInvalid = True

  owner.params = SimpleNamespace(get_bool=lambda key: bool(values.get(key)),
                                get=lambda key: values.get(key), put_bool=put)
  stage = getattr(owner, 'stage_ioniq6_startup_readiness', None)
  assert callable(stage), 'Readiness staging must recheck current native health after persistence'
  assert not stage(owner.CP)
  assert writes == [('ControlsReady', True), ('ControlsReady', False)]
  assert not values['ControlsReady']
