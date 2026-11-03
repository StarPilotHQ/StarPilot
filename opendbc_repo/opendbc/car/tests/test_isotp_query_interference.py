from unittest.mock import patch

import pytest

from opendbc.car import CanData, structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.hyundai.fingerprints import FW_VERSIONS
from opendbc.car.hyundai.values import CAR
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery
from opendbc.car.vin import VIN_UNKNOWN


CAMERA = FW_VERSIONS[CAR.KIA_CARNIVAL_2025][(structs.CarParams.Ecu.fwdCamera, 0x7C4, None)][0]


class DiagnosticBus:
  def __init__(self, replies):
    self.replies = replies
    self.now = 0.
    self.requests = []
    self.pending = []
    self.consecutive = []

  def schedule(self, payload, delay=.01, bus=1):
    if len(payload) <= 7:
      self.pending.append((self.now + delay, CanData(0x7CC, bytes([len(payload)]) + payload, bus)))
    else:
      self.pending.append((self.now + delay, CanData(0x7CC, bytes([0x10, len(payload)]) + payload[:6], bus)))
      self.consecutive = [CanData(0x7CC, bytes([0x20 | (i % 16)]) + payload[j:j+7], bus)
                          for i, j in enumerate(range(6, len(payload), 7), 1)]

  def send(self, packets):
    for address, data, bus in packets:
      assert address == 0x7C4 and bus == 1
      if data[0] == 0x30:
        self.pending.extend((self.now + .01 * i, frame) for i, frame in enumerate(self.consecutive, 1))
        self.consecutive = []
      else:
        request = data[1:1+data[0]]
        self.requests.append(request)
        for payload, delay, response_bus in self.replies(len(self.requests)):
          self.schedule(payload, delay, response_bus)

  def recv(self, wait_for_one=False):
    if wait_for_one:
      self.now += .01
    ready = [frame for deadline, frame in self.pending if deadline <= self.now]
    self.pending = [(deadline, frame) for deadline, frame in self.pending if deadline > self.now]
    return [ready] if ready else []

  def query(self, request=b'\x22\xf1\x00', response=b'\x62', total_timeout=1.):
    with patch('opendbc.car.isotp_parallel_query.time.monotonic', side_effect=lambda: self.now):
      return IsoTpParallelQuery(self.send, self.recv, 1, [0x7C4], [request], [response]).get_data(.05, total_timeout)


@pytest.mark.parametrize('foreign', (b'\x59\x02\x0c\x62\x39\x86\x08', b'\x7f\x19\x78',
                                     b'\x62\xf1\x90x'))
@pytest.mark.parametrize('same_attempt', (False, True))
def test_foreign_diagnostic_reply_cannot_finish_firmware_query(foreign, same_attempt):
  def replies(attempt):
    if attempt == 1:
      result = [(foreign, .01, 1)]
      if same_attempt:
        result.append((b'\x62' + CAMERA, .03, 1))
      return result
    return [(b'\x62' + CAMERA, .01, 1)]
  bus = DiagnosticBus(replies)
  assert bus.query() == {(0x7C4, None): CAMERA}
  assert bus.requests == [b'\x22\xf1\x00'] * (1 if same_attempt else 2)
  assert bus.now < .3


def test_complete_transport_recovery_still_requires_both_existing_firmware_identities():
  bus = DiagnosticBus(lambda attempt: [(b'\x59\x02\x0c\x62\x39\x86\x08', .01, 1)] if attempt == 1
                      else [(b'\x62' + CAMERA, .01, 1)])
  camera = bus.query()[(0x7C4, None)]
  radar = FW_VERSIONS[CAR.KIA_CARNIVAL_2025][(structs.CarParams.Ecu.fwdRadar, 0x7D0, None)][-1]
  fw = [structs.CarParams.CarFw(ecu=ecu, address=address, brand='hyundai', fwVersion=version, bus=1)
        for ecu, address, version in ((structs.CarParams.Ecu.fwdCamera, 0x7C4, camera),
                                      (structs.CarParams.Ecu.fwdRadar, 0x7D0, radar))]
  assert match_fw_to_car(fw, VIN_UNKNOWN, log=False) == (True, {CAR.KIA_CARNIVAL_2025})
  assert not match_fw_to_car(fw[1:], VIN_UNKNOWN, log=False)[1]
  fw[0].fwVersion = camera + b'unknown'
  assert not match_fw_to_car(fw, VIN_UNKNOWN, log=False)[1]


@pytest.mark.parametrize('payload', (b'\x7f\x22\x31', b'\x7f\x22\x21'))
def test_current_request_negative_response_remains_terminal(payload):
  bus = DiagnosticBus(lambda attempt: [(payload, .01, 1)])
  assert bus.query() == {}
  assert len(bus.requests) == 1


def test_continuous_foreign_traffic_gets_only_one_read_retry():
  bus = DiagnosticBus(lambda attempt: [(b'\x59\x02\x0c', .01, 1)])
  assert bus.query() == {}
  assert len(bus.requests) == 2
  assert bus.now < .15


@pytest.mark.parametrize('response_bus', (0, 2, 129, 193))
def test_wrong_bus_and_echo_cannot_supply_identity_or_trigger_retry(response_bus):
  bus = DiagnosticBus(lambda attempt: [(b'\x62' + CAMERA, .01, response_bus)])
  assert bus.query() == {}
  assert len(bus.requests) == 1


def test_actuation_service_is_never_retried():
  bus = DiagnosticBus(lambda attempt: [(b'\x59\x02\x0c', .01, 1)])
  assert bus.query(b'\x28\x83\x03', b'\x68') == {}
  assert bus.requests == [b'\x28\x83\x03']


def test_matching_pending_response_keeps_existing_wait_contract():
  bus = DiagnosticBus(lambda attempt: [(b'\x7f\x22\x78', .01, 1), (b'\x62' + CAMERA, .12, 1)])
  assert bus.query() == {(0x7C4, None): CAMERA}
  assert len(bus.requests) == 1


def test_foreign_pending_cannot_extend_total_deadline():
  bus = DiagnosticBus(lambda attempt: [(b'\x7f\x19\x78', .01, 1)])
  assert bus.query(total_timeout=.075) == {}
  assert len(bus.requests) == 2
  assert bus.now <= .1
