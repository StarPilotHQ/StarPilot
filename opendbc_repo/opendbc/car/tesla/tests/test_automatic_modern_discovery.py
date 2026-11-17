"""Modern Tesla discovery must keep normal wiring without inferring authority."""
from dataclasses import replace
from unittest import mock

import pytest

from opendbc.car import car_helpers, fw_versions, gen_empty_fingerprint
from opendbc.car.can_definitions import CanData
from opendbc.car.ford.discovery import normal_bus_query_brand
from opendbc.car.tesla.discovery import HW1_NORMAL_BUS0_LAYOUT, MODERN_NORMAL_BUS0_LAYOUT, normal_bus0_topology
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.tests.test_automatic_hw1_discovery import AP1Ecu, automatic
from opendbc.car.tesla.tests.test_selected_discovery import VIN
from opendbc.car.tesla.values import CAR
from opendbc.car.vin import VIN_UNKNOWN

FIRMWARE = {
  CAR.TESLA_MODEL_3: b'TeMYG4_Main_0.0.0 (92),E4L015.10.2',
  CAR.TESLA_MODEL_Y: b'TeMYG4_Main_0.0.0 (92),Y4003.10.2',
}


class ModernEcu(AP1Ecu):
  def __init__(self, platform=CAR.TESLA_MODEL_3, *, marker=True, vehicle_bus=False, firmware=None):
    super().__init__(FIRMWARE[platform] if firmware is None else firmware)
    self.layout = dict(MODERN_NORMAL_BUS0_LAYOUT)
    self.layout[2, 0x293] = 8
    if marker:
      self.layout[0, 0x054] = 8
      self.layout[2, 0x489] = 8
    if vehicle_bus:
      self.layout[1, 0x3DF] = 8

  def recv(self, wait_for_one=False):
    self.clock += .001
    values, self.frames = self.frames, []
    return [values + [CanData(address, bytes(length), bus)
                      for (bus, address), length in self.layout.items()]]


@pytest.mark.parametrize('platform', list(FIRMWARE))
@pytest.mark.parametrize('alpha,release', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('vehicle_bus', [False, True])
def test_automatic_modern_tesla_keeps_live_vin_fw_can_and_factory(platform, alpha, release, vehicle_bus):
  peer = ModernEcu(platform, vehicle_bus=vehicle_bus)
  ci, callbacks = automatic(peer, alpha=alpha, release=release)
  assert callbacks and set(callbacks) == {False}
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)
  assert ci.CP.carFingerprint == platform and ci.CP.carVin == VIN
  assert ci.CP.fingerprintSource == car_helpers.CarParams.FingerprintSource.fw
  assert ci.CP.carFw and all(fw.brand == 'tesla' and fw.bus == 0 for fw in ci.CP.carFw)
  assert any(fw.fwVersion == FIRMWARE[platform] for fw in ci.CP.carFw)
  fp = gen_empty_fingerprint()
  for (bus, address), length in peer.layout.items():
    fp[bus][address] = length
  direct = CarInterface.get_params(platform, fp, list(ci.CP.carFw), alpha, release, False)
  for field in ('flags', 'alternativeExperience', 'pcmCruise', 'openpilotLongitudinalControl',
                'alphaLongitudinalAvailable', 'radarUnavailable', 'dashcamOnly'):
    assert getattr(ci.CP, field) == getattr(direct, field)
  assert [cfg.to_dict() for cfg in ci.CP.safetyConfigs] == [cfg.to_dict() for cfg in direct.safetyConfigs]


@pytest.mark.parametrize('source', list(MODERN_NORMAL_BUS0_LAYOUT))
@pytest.mark.parametrize('defect', ['missing', 'length', 'bus', 'echo'])
def test_modern_query_scope_requires_every_source(source, defect):
  fp = {}
  for (bus, address), length in MODERN_NORMAL_BUS0_LAYOUT.items():
    if (bus, address) == source:
      if defect == 'missing':
        continue
      if defect == 'length':
        length -= 1
      else:
        bus += 128 if defect == 'echo' else 1
    fp.setdefault(bus, {})[address] = length
  assert not normal_bus0_topology(fp)


@pytest.mark.parametrize('layout', [HW1_NORMAL_BUS0_LAYOUT, MODERN_NORMAL_BUS0_LAYOUT])
def test_conflicting_lengths_reject_both_tesla_query_layouts(layout):
  (bus, address), length = next(iter(layout.items()))
  rows = [CanData(address, bytes(length - 1), bus),
          *(CanData(address, bytes(length), bus) for (bus, address), length in layout.items())]
  assert normal_bus_query_brand(lambda **kwargs: [rows], lambda recv: bool(recv() and False),
                               tesla_probe=normal_bus0_topology, clock=lambda: 0.) is None


@pytest.mark.parametrize('firmware', [b'', b'unknown-modern-tesla-eps', b'TeMYG4_Main'])
def test_modern_layout_cannot_replace_live_identity(firmware):
  ci, callbacks = automatic(ModernEcu(firmware=firmware))
  assert set(callbacks) == {False}
  assert ci.CP.carFingerprint not in FIRMWARE


def test_modern_layout_cannot_enable_old_steering_encoding():
  ci, callbacks = automatic(ModernEcu(marker=False))
  assert set(callbacks) == {False}
  assert ci.CP.carFingerprint == CAR.TESLA_MODEL_3 and ci.CP.dashcamOnly


def test_missing_vin_keeps_live_firmware_without_switching_wiring():
  peer = ModernEcu()
  reply = peer.reply
  with mock.patch.object(peer, 'reply', side_effect=lambda tx, rx, value: reply(tx, rx, value) if rx != 0x7E8 else None):
    ci, callbacks = automatic(peer)
  assert set(callbacks) == {False} and all(frame.src == 0 for frame in peer.sent)
  assert ci.CP.carVin == VIN_UNKNOWN and ci.CP.carFingerprint == CAR.TESLA_MODEL_3
  assert any(fw.fwVersion == FIRMWARE[CAR.TESLA_MODEL_3] for fw in ci.CP.carFw)


def test_modern_probe_keeps_shared_read_budget():
  calls = []
  assert normal_bus_query_brand(lambda **kwargs: calls.append(kwargs) or [], lambda recv: bool(recv() and False),
                               tesla_probe=normal_bus0_topology, clock=lambda: 0.) is None
  assert len(calls) == 24


@pytest.mark.parametrize('registry', ['configured', 'effective'])
@pytest.mark.parametrize('change', ['bus1', 'empty'])
def test_automatic_scope_requires_unchanged_query_topology(registry, change):
  if registry == 'configured':
    target, name = fw_versions.FW_QUERY_CONFIGS['tesla'], 'requests'
    altered = [replace(request, bus=1) for request in target.requests] if change == 'bus1' else []
  else:
    target, name = fw_versions, 'REQUESTS'
    altered = [(brand, config, replace(request, bus=1) if brand == 'tesla' else request)
               for brand, config, request in fw_versions.REQUESTS] if change == 'bus1' else [
                 item for item in fw_versions.REQUESTS if item[0] != 'tesla']
  with mock.patch.object(target, name, altered), \
       mock.patch.object(car_helpers, 'normal_bus_query_brand', return_value=None) as probe, \
       mock.patch.object(car_helpers, 'get_present_ecus', return_value=set()), \
       mock.patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]):
    automatic(ModernEcu())
  assert probe.call_args.kwargs['tesla_probe'] is None


@pytest.mark.parametrize('platform', list(FIRMWARE))
def test_discovery_keeps_native_can_fault_blocking(platform):
  from openpilot.cereal import log
  from openpilot.starpilot.aol.panda_faults import blocking_fault_list
  ci, _ = automatic(ModernEcu(platform))
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], ci.CP)
  assert blocking_fault_list([log.PandaState.FaultType.relayMalfunction], ci.CP)
