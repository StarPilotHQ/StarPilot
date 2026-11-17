"""Selected Tesla discovery uses its declared wiring without changing authority."""
from dataclasses import replace
from unittest import mock

import pytest

from opendbc.car import car_helpers, fw_versions, gen_empty_fingerprint
from opendbc.car.can_definitions import CanData
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR

VIN = '7SAYGDEE0PF000001'
FIRMWARE = b'TeMYG4_Main_0.0.0 (92),Y4003.10.2'


class TeslaEcu:
  def __init__(self):
    self.clock = 0.
    self.frames = []
    self.pending = {}
    self.sent = []

  def send(self, messages):
    self.sent.extend(messages)
    for message in messages:
      if message.src != 0:
        continue
      payload = message.dat
      if payload[0] >> 4 == 3:
        self.frames.extend(self.pending.pop(message.address, []))
      elif payload[0] >> 4 == 0:
        request = payload[1:1 + payload[0]]
        if message.address in (0x7DF, 0x7E0) and request == b'\x22\xf1\x90':
          self.reply(0x7E0, 0x7E8, b'\x62\xf1\x90' + VIN.encode())
        elif message.address == 0x730 and request == b'\x3e\x00':
          self.reply(0x730, 0x738, b'\x7e\x00')
        elif message.address == 0x730 and request == b'\x22\xf1\x95':
          self.reply(0x730, 0x738, b'\x62\xf1\x95' + FIRMWARE)

  def reply(self, tx, rx, value):
    if len(value) < 8:
      self.frames.append(CanData(rx, bytes([len(value)]) + value + bytes(7 - len(value)), 0))
    else:
      self.frames.append(CanData(rx, bytes([0x10 | len(value) >> 8, len(value) & 0xFF]) + value[:6], 0))
      self.pending[tx] = [CanData(rx, bytes([0x20 | (i + 1) % 16]) + value[start:start + 7] +
                                 bytes(max(0, 7 - len(value[start:start + 7]))), 0)
                          for i, start in enumerate(range(6, len(value), 7))]

  def recv(self, wait_for_one=False):
    self.clock += .001
    values, self.frames = self.frames, []
    return [values + [CanData(0x054, bytes(8), 0), CanData(0x489, bytes(8), 2), CanData(0x293, bytes(8), 2)]]


def discover(peer, candidate, *, alpha=False, release=False, cached=None, env_selection=False, skip_fw=False):
  callbacks = []
  with mock.patch.dict(car_helpers.os.environ, {'FINGERPRINT': str(candidate) if env_selection else '',
                                               'SKIP_FW_QUERY': '1' if skip_fw else '', 'DISABLE_FW_CACHE': ''}), \
       mock.patch.object(car_helpers.time, 'monotonic', side_effect=lambda: peer.clock), \
       mock.patch.object(car_helpers.time, 'sleep', side_effect=lambda seconds: setattr(peer, 'clock', peer.clock + seconds)):
    ci = car_helpers.get_car(peer.recv, peer.send, callbacks.append, alpha, release, cached,
                            forced_candidate=None if env_selection else candidate)
  return ci, callbacks


@pytest.mark.parametrize('candidate', list(CAR))
@pytest.mark.parametrize('alpha,release', [(False, False), (True, False), (True, True)])
def test_registered_tesla_selection_keeps_actual_vin_fw_can_and_factory(candidate, alpha, release):
  peer = TeslaEcu()
  ci, callbacks = discover(peer, candidate, alpha=alpha, release=release)
  assert callbacks and set(callbacks) == {False}
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)
  assert ci.CP.carFingerprint == candidate and ci.CP.carVin == VIN
  assert ci.CP.fingerprintSource == car_helpers.CarParams.FingerprintSource.fixed
  assert ci.CP.carFw and all(fw.brand == 'tesla' and fw.bus == 0 for fw in ci.CP.carFw)
  assert any(fw.fwVersion == FIRMWARE for fw in ci.CP.carFw)
  fp = gen_empty_fingerprint()
  fp[0][0x054], fp[2][0x489], fp[2][0x293] = 8, 8, 8
  direct = CarInterface.get_params(candidate, fp, list(ci.CP.carFw), alpha, release, False)
  for field in ('flags', 'alternativeExperience', 'pcmCruise', 'openpilotLongitudinalControl',
                'alphaLongitudinalAvailable', 'radarUnavailable', 'dashcamOnly'):
    assert getattr(ci.CP, field) == getattr(direct, field)
  assert [cfg.to_dict() for cfg in ci.CP.safetyConfigs] == [cfg.to_dict() for cfg in direct.safetyConfigs]


def test_environment_selection_uses_same_query_wiring():
  peer = TeslaEcu()
  _, callbacks = discover(peer, CAR.TESLA_MODEL_Y, env_selection=True)
  assert set(callbacks) == {False} and all(frame.src == 0 for frame in peer.sent)


@pytest.mark.parametrize('registry', ['configured', 'effective'])
@pytest.mark.parametrize('change', ['bus1', 'empty'])
def test_tesla_scope_requires_unchanged_declared_and_effective_query_bus(registry, change):
  if registry == 'configured':
    target, name = fw_versions.FW_QUERY_CONFIGS['tesla'], 'requests'
    altered = [replace(request, bus=1) for request in target.requests] if change == 'bus1' else []
  else:
    target, name = fw_versions, 'REQUESTS'
    altered = [(brand, config, replace(request, bus=1) if brand == 'tesla' else request)
               for brand, config, request in fw_versions.REQUESTS] if change == 'bus1' else [
                 item for item in fw_versions.REQUESTS if item[0] != 'tesla']
  with mock.patch.object(target, name, altered):
    assert not car_helpers._tesla_fw_uses_normal_bus0()
    with pytest.raises(ValueError, match='Tesla discovery topology'):
      car_helpers.fingerprint(mock.Mock(), mock.Mock(), mock.Mock(), None, known_tesla_bus0=True)


def test_stale_other_vehicle_cache_cannot_replace_live_tesla_responses():
  peer = TeslaEcu()
  stale = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_3)
  stale.carVin = VIN
  stale.carFw = [{'ecu': 'eps', 'fwVersion': b'stale', 'brand': 'tesla', 'bus': 1}]
  ci, _ = discover(peer, CAR.TESLA_MODEL_Y, cached=stale)
  assert peer.sent and all(fw.fwVersion == FIRMWARE for fw in ci.CP.carFw)


def test_discovery_does_not_waive_recorded_native_fault_or_mismatch():
  from openpilot.cereal import log
  from openpilot.starpilot.aol.panda_faults import blocking_fault_list
  cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_Y)
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)
  assert blocking_fault_list([log.PandaState.FaultType.relayMalfunction], cp)


def test_matching_cache_keeps_live_can_layout_without_repeating_queries():
  original, _ = discover(TeslaEcu(), CAR.TESLA_MODEL_Y)
  peer = TeslaEcu()
  ci, callbacks = discover(peer, CAR.TESLA_MODEL_Y, cached=original.CP)
  assert not peer.sent and callbacks == [False]
  assert ci.CP.carVin == VIN and ci.CP.carFw[0].fwVersion == FIRMWARE
  assert ci.CP.flags == original.CP.flags


def test_explicit_skip_still_keeps_vehicle_selection_and_live_can():
  peer = TeslaEcu()
  ci, callbacks = discover(peer, CAR.TESLA_MODEL_Y, skip_fw=True)
  assert not peer.sent and callbacks == [False]
  assert ci.CP.carFingerprint == CAR.TESLA_MODEL_Y and not ci.CP.carFw
