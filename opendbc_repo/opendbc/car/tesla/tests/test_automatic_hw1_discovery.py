"""AP1 discovery must collect live identity without probing unrelated OBD wiring."""
from unittest import mock

import pytest
from opendbc.car import car_helpers
from opendbc.car.can_definitions import CanData
from opendbc.car.tesla.discovery import HW1_NORMAL_BUS0_LAYOUT, hw1_normal_bus0_topology
from opendbc.car.tesla.tests.test_selected_discovery import TeslaEcu
from opendbc.car.tesla.values import CAR

HW1_FIRMWARE = b'1016704-00-HAA' + b'\x00' * 10


class AP1Ecu(TeslaEcu):
  def __init__(self, firmware=HW1_FIRMWARE):
    super().__init__()
    self.firmware = firmware

  def send(self, messages):
    for message in messages:
      payload = message.dat
      if (message.src == 0 and message.address == 0x730 and payload[0] >> 4 == 0 and
          payload[1:1 + payload[0]] == b'\x22\xf1\x95'):
        self.sent.append(message)
        self.reply(0x730, 0x738, b'\x62\xf1\x95' + self.firmware)
      else:
        super().send([message])

  def recv(self, wait_for_one=False):
    self.clock += .001
    values, self.frames = self.frames, []
    return [values + [CanData(address, bytes(length), bus)
                      for (bus, address), length in HW1_NORMAL_BUS0_LAYOUT.items()]]


def automatic(peer, *, alpha=False, release=False):
  callbacks = []
  with mock.patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       mock.patch.object(car_helpers.time, 'monotonic', side_effect=lambda: peer.clock), \
       mock.patch.object(car_helpers.time, 'sleep', side_effect=lambda seconds: setattr(peer, 'clock', peer.clock + seconds)):
    result = car_helpers.get_car(peer.recv, peer.send, callbacks.append, alpha, release)
  return result, callbacks


@pytest.mark.parametrize('alpha,release', [(False, False), (True, False), (True, True)])
def test_actual_automatic_ap1_vin_fw_can_and_factory_use_normal_wiring(alpha, release):
  peer = AP1Ecu()
  ci, callbacks = automatic(peer, alpha=alpha, release=release)
  assert callbacks and set(callbacks) == {False}
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)
  assert ci.CP.carFingerprint == CAR.TESLA_MODEL_S_HW1
  assert ci.CP.fingerprintSource == car_helpers.CarParams.FingerprintSource.fw
  assert ci.CP.carFw and all(fw.brand == 'tesla' and fw.bus == 0 for fw in ci.CP.carFw)
  assert any(fw.fwVersion == HW1_FIRMWARE for fw in ci.CP.carFw)
  assert ci.CP.safetyConfigs[0].safetyParam == 16 + int(alpha)
  assert ci.CP.openpilotLongitudinalControl == alpha and ci.CP.pcmCruise != alpha


@pytest.mark.parametrize('source', list(HW1_NORMAL_BUS0_LAYOUT))
@pytest.mark.parametrize('defect', ['missing', 'length', 'bus', 'echo'])
def test_ap1_wiring_scope_requires_each_real_source(source, defect):
  fp = {}
  for (bus, address), length in HW1_NORMAL_BUS0_LAYOUT.items():
    if (bus, address) == source:
      if defect == 'missing':
        continue
      if defect == 'length':
        length -= 1
      else:
        bus += 128 if defect == 'echo' else 1
    fp.setdefault(bus, {})[address] = length
  assert not hw1_normal_bus0_topology(fp)


def test_conflicting_lengths_cannot_be_hidden_by_later_valid_ap1_frames():
  from opendbc.car.ford.discovery import normal_bus_query_brand
  values = [CanData(address, bytes(length), bus) for (bus, address), length in HW1_NORMAL_BUS0_LAYOUT.items()]
  packets = [CanData(0x108, bytes(7), 0), *values]
  result = normal_bus_query_brand(lambda **kwargs: [packets], lambda recv: bool(recv() and False),
                                  tesla_probe=hw1_normal_bus0_topology, clock=lambda: 0.)
  assert result is None


@pytest.mark.parametrize('firmware', [b'', b'1016704-00-HAA', b'\x10\x00B'])
def test_ap1_layout_never_substitutes_for_live_firmware_identity(firmware):
  ci, _ = automatic(AP1Ecu(firmware))
  assert ci.CP.carFingerprint != CAR.TESLA_MODEL_S_HW1


def test_passive_ap1_layout_keeps_existing_shared_read_budget():
  from opendbc.car.ford.discovery import normal_bus_query_brand
  calls = []
  assert normal_bus_query_brand(lambda **kwargs: calls.append(kwargs) or [], lambda recv: bool(recv() and False),
                                tesla_probe=hw1_normal_bus0_topology, clock=lambda: 0.) is None
  assert len(calls) == 24
